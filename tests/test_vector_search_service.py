import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.core.config import settings
from app.db.database import Base
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.user import User
from app.services.embedding_service import VECTOR_DIMENSIONS
from app.services.vector_search_service import VectorSearchService


@pytest.fixture
def search_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        users = [
            User(
                name=f"User {index}",
                email=f"search-user-{index}@example.com",
                password_hash="not-a-real-password-hash",
            )
            for index in (1, 2)
        ]
        session.add_all(users)
        session.flush()
        documents = [
            Document(
                user_id=users[index].id,
                filename=f"doc-{index}.pdf",
                original_filename=f"doc-{index}.pdf",
                file_type="pdf",
            )
            for index in range(2)
        ]
        session.add_all(documents)
        session.flush()
        matching_vector = [0.0] * VECTOR_DIMENSIONS
        matching_vector[0] = 1.0
        unrelated_vector = [0.0] * VECTOR_DIMENSIONS
        unrelated_vector[1] = 1.0
        session.add_all(
            [
                DocumentChunk(
                    document_id=documents[0].id,
                    chunk_index=0,
                    content="Owned relevant content",
                    page_number=4,
                    embedding=matching_vector,
                ),
                DocumentChunk(
                    document_id=documents[0].id,
                    chunk_index=1,
                    content="Owned less relevant content",
                    page_number=5,
                    embedding=unrelated_vector,
                ),
                DocumentChunk(
                    document_id=documents[1].id,
                    chunk_index=0,
                    content="Other user's matching content",
                    page_number=9,
                    embedding=matching_vector,
                ),
            ]
        )
        session.commit()
        yield session, users[0].id, documents[0].id, documents[1].id
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_vector_search_orders_by_similarity_and_only_returns_owner_chunks(
    search_session, monkeypatch
):
    session, user_id, _, _ = search_session
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite://")
    query_embedding = [0.0] * VECTOR_DIMENSIONS
    query_embedding[0] = 1.0

    results = VectorSearchService().search(
        session,
        user_id=user_id,
        query_embedding=query_embedding,
        top_k=1,
    )

    assert len(results) == 1
    assert results[0]["content"] == "Owned relevant content"
    assert results[0]["filename"] == "doc-0.pdf"
    assert results[0]["page_number"] == 4
    assert results[0]["similarity_score"] == pytest.approx(1.0)


def test_vector_search_restricts_to_requested_owned_document(search_session, monkeypatch):
    session, user_id, own_document_id, _ = search_session
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite://")
    query_embedding = [0.0] * VECTOR_DIMENSIONS
    query_embedding[0] = 1.0

    results = VectorSearchService().search(
        session,
        user_id=user_id,
        query_embedding=query_embedding,
        document_ids=[own_document_id],
    )

    assert results
    assert {result["document_id"] for result in results} == {own_document_id}


def test_vector_search_rejects_document_ids_not_owned_by_user(
    search_session, monkeypatch
):
    session, user_id, _, other_document_id = search_session
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite://")

    with pytest.raises(HTTPException) as exception:
        VectorSearchService().search(
            session,
            user_id=user_id,
            query_embedding=[0.0] * VECTOR_DIMENSIONS,
            document_ids=[other_document_id],
        )

    assert exception.value.status_code == 404


def test_postgres_query_uses_pgvector_cosine_operator(monkeypatch):
    class EmptyResult:
        def mappings(self):
            return self

        def all(self):
            return []

    class QueryCaptureSession:
        statement = None

        def execute(self, statement):
            self.statement = statement
            return EmptyResult()

    monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+psycopg://user:pass@localhost/db")
    session = QueryCaptureSession()
    VectorSearchService().search(
        session,
        user_id=12,
        query_embedding=[0.0] * VECTOR_DIMENSIONS,
        top_k=3,
    )

    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert "<=>" in str(compiled)
    assert "documents.user_id" in str(compiled)
    assert compiled.params["user_id_1"] == 12
