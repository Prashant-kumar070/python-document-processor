from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.chunking_service import ChunkingService
from app.services.document_service import DocumentService
from app.services.embedding_service import (
    VECTOR_DIMENSIONS,
    BaseEmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingService,
    GeminiEmbeddingProvider,
    OpenAIEmbeddingProvider,
)


class RecordingEmbeddingProvider(BaseEmbeddingProvider):
    def __init__(self):
        self.single_inputs = []
        self.batch_inputs = []

    def generate_embedding(self, text):
        self.single_inputs.append(text)
        return [0.25] * VECTOR_DIMENSIONS

    def generate_embeddings(self, texts):
        self.batch_inputs.append(texts)
        return [[float(index)] * VECTOR_DIMENSIONS for index, _ in enumerate(texts)]


def test_openai_provider_requires_credentials_instead_of_using_mock_vectors(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    provider = OpenAIEmbeddingProvider()

    with pytest.raises(EmbeddingProviderError, match="credentials are required"):
        provider.generate_embedding("test input")


def test_openai_provider_returns_batch_embeddings_in_response_order():
    expected_vectors = [[float(index)] * VECTOR_DIMENSIONS for index in range(2)]
    client = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda **_: SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=expected_vectors[1]),
                    SimpleNamespace(index=0, embedding=expected_vectors[0]),
                ]
            )
        )
    )
    provider = OpenAIEmbeddingProvider(api_key="test-key", client=client)

    vectors = provider.generate_embeddings(["first", "second"])

    assert vectors == expected_vectors


def test_gemini_provider_uses_retrieval_tasks_model_and_configured_dimensions():
    calls = []
    expected_vectors = [
        [float(index)] * VECTOR_DIMENSIONS
        for index in range(2)
    ]

    class FakeModels:
        def embed_content(self, **kwargs):
            calls.append(kwargs)
            contents = kwargs["contents"]
            vectors = expected_vectors if isinstance(contents, list) else expected_vectors[:1]
            return SimpleNamespace(
                embeddings=[
                    SimpleNamespace(values=vector) for vector in vectors
                ]
            )

    class FakeClient:
        models = FakeModels()

    provider = GeminiEmbeddingProvider(
        api_key="test-key",
        model="gemini-embedding-001",
        dimensions=VECTOR_DIMENSIONS,
        client=FakeClient(),
    )

    query_vector = provider.generate_embedding("question")
    document_vectors = provider.generate_embeddings(["chunk 1", "chunk 2"])

    assert query_vector == expected_vectors[0]
    assert document_vectors == expected_vectors
    assert calls[0]["model"] == "gemini-embedding-001"
    assert calls[0]["contents"] == "question"
    assert calls[0]["config"].task_type == "RETRIEVAL_QUERY"
    assert calls[0]["config"].output_dimensionality == VECTOR_DIMENSIONS
    assert calls[1]["contents"] == ["chunk 1", "chunk 2"]
    assert calls[1]["config"].task_type == "RETRIEVAL_DOCUMENT"


def test_embedding_service_selects_gemini_provider(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "")

    service = EmbeddingService()

    assert isinstance(service.provider, GeminiEmbeddingProvider)
    with pytest.raises(EmbeddingProviderError, match="Gemini API credentials"):
        service.generate_embedding("question")


def test_embedding_service_rejects_incomplete_batches_and_wrong_dimensions():
    class ShortBatchProvider(RecordingEmbeddingProvider):
        def generate_embeddings(self, texts):
            return []

    with pytest.raises(EmbeddingProviderError, match="0 vectors for 1 texts"):
        EmbeddingService(ShortBatchProvider()).generate_embeddings(["one"])

    class WrongDimensionProvider(RecordingEmbeddingProvider):
        def generate_embedding(self, text):
            return [0.1]

    with pytest.raises(EmbeddingProviderError, match="Expected an embedding"):
        EmbeddingService(WrongDimensionProvider()).generate_embedding("one")


def test_document_processing_generates_and_passes_one_embedding_per_chunk(
    monkeypatch, tmp_path
):
    class FakeDocumentRepository:
        def __init__(self):
            self.document = SimpleNamespace(
                id=7,
                filename="source.pdf",
                processed_at=None,
                status="PENDING",
                processing_error=None,
            )

        def get_by_id(self, document_id, user_id=None):
            return self.document

        def update_status(self, document, *, status, processing_error=None):
            document.status = status
            document.processing_error = processing_error
            return document

    class FakeChunkRepository:
        def __init__(self):
            self.saved_chunks = []

        def delete_document_chunks(self, document_id):
            self.saved_chunks = []

        def replace_document_chunks(self, document_id, chunks):
            self.saved_chunks = chunks
            return chunks

    document_repository = FakeDocumentRepository()
    chunk_repository = FakeChunkRepository()
    provider = RecordingEmbeddingProvider()
    service = DocumentService(db=None)
    service.document_repository = document_repository
    service.chunk_repository = chunk_repository
    service.embedding_service = EmbeddingService(provider)
    service.chunking_service = ChunkingService(chunk_size=16, chunk_overlap=0)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "source.pdf").write_bytes(b"test")
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(
        "app.services.document_service.parse_document_text",
        lambda _: [{"page_number": 5, "text": "Alpha sentence. Beta sentence."}],
    )

    service.process_document(document_id=7, user_id=11)

    assert provider.batch_inputs == [["Alpha sentence.", "Beta sentence."]]
    assert len(chunk_repository.saved_chunks) == 2
    assert all(len(chunk["embedding"]) == VECTOR_DIMENSIONS for chunk in chunk_repository.saved_chunks)
    assert [chunk["page_number"] for chunk in chunk_repository.saved_chunks] == [5, 5]
    assert document_repository.document.status == "COMPLETED"
