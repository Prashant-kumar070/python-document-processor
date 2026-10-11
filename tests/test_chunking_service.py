import pytest

from app.services.chunking_service import ChunkingService


def test_chunk_text_preserves_sentence_boundaries_and_overlap():
    service = ChunkingService(chunk_size=30, chunk_overlap=10)

    chunks = service.chunk_text("Alpha beta gamma delta. Epsilon zeta eta theta.")

    assert len(chunks) == 2
    assert chunks[0] == "Alpha beta gamma delta."
    assert chunks[1].startswith("delta. Epsilon")
    assert all(len(chunk) <= 30 for chunk in chunks)


def test_chunk_pages_keeps_page_numbers_and_global_chunk_indexes():
    service = ChunkingService(chunk_size=18, chunk_overlap=0)

    chunks = service.chunk_pages([
        {"page_number": 3, "text": "Alpha sentence. Beta sentence."},
        {"page_number": 4, "text": "Gamma sentence."},
    ])

    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
    assert [chunk["page_number"] for chunk in chunks] == [3, 3, 4]
    assert all(len(chunk["content"]) <= 18 for chunk in chunks)


def test_long_sentence_is_split_without_exceeding_chunk_size():
    service = ChunkingService(chunk_size=20, chunk_overlap=4)

    chunks = service.chunk_text("abcdefghijklmnopqrstuvwxy")

    assert len(chunks) > 1
    assert all(len(chunk) <= 20 for chunk in chunks)


@pytest.mark.parametrize(
    ("chunk_size", "chunk_overlap"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_invalid_chunk_configuration_is_rejected(chunk_size, chunk_overlap):
    with pytest.raises(ValueError):
        ChunkingService(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
