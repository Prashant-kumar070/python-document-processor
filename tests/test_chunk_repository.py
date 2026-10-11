import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.db.models.user import User
from app.repositories.chunk_repository import ChunkRepository
from app.services.embedding_service import VECTOR_DIMENSIONS


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        session.add(
            User(
                name="Test user",
                email="chunk-repository@example.com",
                password_hash="not-a-real-password-hash",
            )
        )
        session.commit()
        user = session.query(User).one()
        document = Document(
            user_id=user.id,
            filename="document.pdf",
            original_filename="document.pdf",
            file_type="pdf",
        )
        session.add(document)
        session.commit()
        yield session, document.id
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_replace_document_chunks_persists_vector_values(db_session):
    session, document_id = db_session
    vector = [0.125] * VECTOR_DIMENSIONS
    repository = ChunkRepository(session)

    repository.replace_document_chunks(
        document_id,
        [
            {
                "chunk_index": 0,
                "content": "A chunk with its generated embedding.",
                "page_number": 2,
                "embedding": vector,
            }
        ],
    )

    stored_chunk = session.query(DocumentChunk).one()
    assert stored_chunk.embedding == vector
    assert stored_chunk.page_number == 2


def test_replace_document_chunks_rolls_back_failed_replacement(db_session):
    session, document_id = db_session
    repository = ChunkRepository(session)
    original_vector = [0.25] * VECTOR_DIMENSIONS
    repository.replace_document_chunks(
        document_id,
        [
            {
                "chunk_index": 0,
                "content": "Original chunk",
                "embedding": original_vector,
            }
        ],
    )

    with pytest.raises(IntegrityError):
        repository.replace_document_chunks(
            document_id,
            [
                {
                    "chunk_index": 0,
                    "content": "Replacement chunk one",
                    "embedding": original_vector,
                },
                {
                    "chunk_index": 0,
                    "content": "Replacement chunk two",
                    "embedding": original_vector,
                },
            ],
        )

    chunks = session.query(DocumentChunk).all()
    assert len(chunks) == 1
    assert chunks[0].content == "Original chunk"
    assert chunks[0].embedding == original_vector
