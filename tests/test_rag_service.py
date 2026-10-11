from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.database import Base
from app.db.models.chunk import DocumentChunk
from app.db.models.conversation import Conversation
from app.db.models.document import Document
from app.db.models.message import Message
from app.db.models.message_source import MessageSource
from app.db.models.user import User
from app.services.embedding_service import VECTOR_DIMENSIONS
from app.services.rag_service import (
    INSUFFICIENT_CONTEXT_ANSWER,
    RagService,
)


@pytest.fixture
def rag_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        user = User(
            name="RAG user",
            email="rag-user@example.com",
            password_hash="not-a-real-password-hash",
        )
        session.add(user)
        session.flush()
        document = Document(
            user_id=user.id,
            filename="authentication.pdf",
            original_filename="authentication.pdf",
            file_type="pdf",
        )
        session.add(document)
        session.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            chunk_index=0,
            content="JWT tokens authenticate requests using a signed bearer token.",
            page_number=4,
            embedding=[0.1] * VECTOR_DIMENSIONS,
        )
        session.add(chunk)
        session.commit()
        yield session, user.id, document, chunk
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


class RecordingEmbeddingService:
    def __init__(self):
        self.questions = []

    def generate_embedding(self, question):
        self.questions.append(question)
        return [0.2] * VECTOR_DIMENSIONS


class FixedVectorSearchService:
    def __init__(self, chunks):
        self.chunks = chunks
        self.arguments = None

    def search(self, db, **kwargs):
        self.arguments = kwargs
        return self.chunks


class RecordingLLMService:
    def __init__(self, answer="JWT uses signed bearer tokens [Source 1]."):
        self.answer = answer
        self.system_instruction = None
        self.messages = None

    def generate_answer(self, *, system_instruction, messages):
        self.system_instruction = system_instruction
        self.messages = messages
        return self.answer


def test_rag_generates_question_embedding_and_persists_actual_sources(
    rag_session, monkeypatch
):
    session, user_id, document, chunk = rag_session
    retrieved = [
        {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "filename": document.original_filename,
            "page_number": chunk.page_number,
            "content": chunk.content,
            "similarity_score": 0.91,
        }
    ]
    embedding_service = RecordingEmbeddingService()
    search_service = FixedVectorSearchService(retrieved)
    llm_service = RecordingLLMService()
    monkeypatch.setattr(settings, "MIN_SIMILARITY_SCORE", 0.30)
    monkeypatch.setattr(settings, "TOP_K", 4)

    response = RagService(
        session,
        embedding_service=embedding_service,
        vector_search_service=search_service,
        llm_service=llm_service,
    ).answer_question(
        user_id=user_id,
        question="How does authentication work?",
        document_ids=[document.id],
    )

    assert embedding_service.questions == ["How does authentication work?"]
    assert search_service.arguments["user_id"] == user_id
    assert search_service.arguments["query_embedding"] == [0.2] * VECTOR_DIMENSIONS
    assert search_service.arguments["document_ids"] == [document.id]
    assert search_service.arguments["top_k"] == 4
    assert "Page: 4" in llm_service.messages[-1]["content"]
    assert f"Chunk ID: {chunk.id}" in llm_service.messages[-1]["content"]
    assert chunk.content in llm_service.messages[-1]["content"]
    assert "using ONLY" in llm_service.system_instruction
    assert response["sources"] == [
        {
            "document_id": document.id,
            "filename": "authentication.pdf",
            "page_number": 4,
            "chunk_id": chunk.id,
            "similarity_score": 0.91,
            "content": chunk.content,
        }
    ]

    assistant_message = (
        session.query(Message)
        .filter(Message.role == "assistant")
        .one()
    )
    stored_source = session.query(MessageSource).one()
    assert stored_source.message_id == assistant_message.id
    assert stored_source.document_chunk_id == chunk.id
    assert stored_source.similarity_score == pytest.approx(0.91)


def test_rag_skips_llm_and_citations_when_context_is_below_threshold(
    rag_session, monkeypatch
):
    session, user_id, document, chunk = rag_session
    search_service = FixedVectorSearchService(
        [
            {
                "chunk_id": chunk.id,
                "document_id": document.id,
                "filename": document.original_filename,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "similarity_score": 0.29,
            }
        ]
    )
    llm_service = RecordingLLMService()
    monkeypatch.setattr(settings, "MIN_SIMILARITY_SCORE", 0.30)

    response = RagService(
        session,
        embedding_service=RecordingEmbeddingService(),
        vector_search_service=search_service,
        llm_service=llm_service,
    ).answer_question(user_id=user_id, question="What is unrelated?")

    assert response["answer"] == INSUFFICIENT_CONTEXT_ANSWER
    assert response["sources"] == []
    assert llm_service.messages is None
    assert session.query(MessageSource).count() == 0
    assert session.query(Message).filter(Message.role == "assistant").one().content == INSUFFICIENT_CONTEXT_ANSWER


def test_rag_includes_only_bounded_recent_history(rag_session, monkeypatch):
    session, user_id, document, chunk = rag_session
    conversation = Conversation(user_id=user_id, title="Existing conversation")
    session.add(conversation)
    session.flush()
    base_time = datetime.now(timezone.utc)
    session.add_all(
        [
            Message(
                conversation_id=conversation.id,
                role="user",
                content=f"old message {index}",
                created_at=base_time + timedelta(seconds=index),
            )
            for index in range(3)
        ]
    )
    session.commit()
    search_service = FixedVectorSearchService(
        [
            {
                "chunk_id": chunk.id,
                "document_id": document.id,
                "filename": document.original_filename,
                "page_number": chunk.page_number,
                "content": chunk.content,
                "similarity_score": 0.91,
            }
        ]
    )
    llm_service = RecordingLLMService()
    monkeypatch.setattr(settings, "CONVERSATION_HISTORY_LIMIT", 2)
    monkeypatch.setattr(settings, "MIN_SIMILARITY_SCORE", 0.30)

    RagService(
        session,
        embedding_service=RecordingEmbeddingService(),
        vector_search_service=search_service,
        llm_service=llm_service,
    ).answer_question(
        user_id=user_id,
        question="And what about renewal?",
        conversation_id=conversation.id,
    )

    assert len(llm_service.messages) == 3
    assert [message["content"] for message in llm_service.messages[:2]] == [
        "old message 1",
        "old message 2",
    ]
    assert llm_service.messages[-1]["role"] == "user"
    assert "QUESTION:\nAnd what about renewal?" in llm_service.messages[-1]["content"]
