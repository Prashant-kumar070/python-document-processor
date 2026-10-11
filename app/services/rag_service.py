from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.conversation_repository import ConversationRepository
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.vector_search_service import VectorSearchService
from app.services.vector_service import VectorService

INSUFFICIENT_CONTEXT_ANSWER = (
    "The uploaded documents do not contain enough information to answer this question."
)

SYSTEM_INSTRUCTION = (
    "You are a document assistant. Answer the user's question using ONLY the current "
    "document context provided in the latest user message. Previous conversation is "
    "included only to resolve follow-up references; it is not evidence. If the current "
    "document context does not contain enough information, say that the uploaded "
    "documents do not contain enough information. Do not invent information. When "
    "using information from a source, cite its exact source number, for example "
    "[Source 1]. Never cite a source number that is not present in the current context."
)


class RagService:
    def __init__(
        self,
        db: Session,
        vector_service: VectorService | None = None,
        llm_service: LLMService | None = None,
        embedding_service: EmbeddingService | None = None,
        vector_search_service: VectorSearchService | None = None,
    ):
        self.db = db
        self.embedding_service = embedding_service or EmbeddingService()
        self.vector_search_service = vector_search_service or VectorSearchService()
        self.legacy_vector_service = vector_service
        self.llm_service = llm_service or LLMService()
        self.conversation_repository = ConversationRepository(db)

    def answer_question(
        self,
        *,
        user_id: int,
        question: str,
        conversation_id: int | None = None,
        document_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        conversation = None
        history = []
        if conversation_id is not None:
            conversation = self.conversation_repository.get_for_user(
                conversation_id,
                user_id,
            )
            if conversation is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found.",
                )
            history = self.conversation_repository.get_recent_messages(
                conversation.id,
                settings.CONVERSATION_HISTORY_LIMIT,
            )

        if self.legacy_vector_service is not None:
            retrieved_chunks = self.legacy_vector_service.search(
                self.db,
                user_id=user_id,
                question=question,
                document_ids=document_ids,
                top_k=settings.TOP_K,
            )
        else:
            question_embedding = self.embedding_service.generate_embedding(question)
            retrieved_chunks = self.vector_search_service.search(
                self.db,
                user_id=user_id,
                query_embedding=question_embedding,
                document_ids=document_ids,
                top_k=settings.TOP_K,
            )

        chunks = [
            chunk
            for chunk in retrieved_chunks
            if float(chunk.get("similarity_score", 0.0))
            >= settings.MIN_SIMILARITY_SCORE
        ]
        if chunks:
            answer = self.llm_service.generate_answer(
                system_instruction=SYSTEM_INSTRUCTION,
                messages=[
                    *[
                        {"role": message.role, "content": message.content}
                        for message in history
                    ],
                    {
                        "role": "user",
                        "content": self._build_user_message(question, chunks),
                    },
                ],
            )
        else:
            answer = INSUFFICIENT_CONTEXT_ANSWER

        source_entries = [
            {
                "document_chunk_id": chunk["chunk_id"],
                "similarity_score": float(chunk["similarity_score"]),
            }
            for chunk in chunks
        ]
        try:
            saved_conversation = self.conversation_repository.save_exchange(
                user_id=user_id,
                conversation_id=conversation_id,
                title=question[:50] or "New conversation",
                question=question,
                answer=answer,
                source_entries=source_entries,
            )
        except LookupError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            ) from exc

        return {
            "answer": answer,
            "conversation_id": saved_conversation.id,
            "sources": [
                {
                    "document_id": chunk["document_id"],
                    "filename": chunk.get("filename"),
                    "page_number": chunk.get("page_number"),
                    "chunk_id": chunk["chunk_id"],
                    "similarity_score": float(chunk["similarity_score"]),
                    "content": chunk.get("content") or chunk.get("chunk_content", ""),

                }
                for chunk in chunks
            ],
        }

    @staticmethod
    def _build_user_message(question: str, chunks: list[dict[str, Any]]) -> str:
        context_sections = []
        for index, chunk in enumerate(chunks, start=1):
            document_name = chunk.get("filename") or "unknown document"
            page = chunk.get("page_number")
            page_label = str(page) if page is not None else "unknown"
            context_sections.append(
                f"[SOURCE {index}]\n"
                f"Document: {document_name}\n"
                f"Page: {page_label}\n"
                f"Chunk ID: {chunk['chunk_id']}\n"
                f"Content:\n{chunk.get('content', chunk.get('chunk_content', ''))}"
            )

        context = "\n\n".join(context_sections)
        return (
            "CURRENT DOCUMENT CONTEXT:\n"
            f"{context}\n\n"
            f"QUESTION:\n{question}"
        )
