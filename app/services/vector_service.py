from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.embedding_service import EmbeddingService
from app.services.vector_search_service import VectorSearchService


class VectorService:
    """Compatibility adapter; the RAG service will own question embedding generation."""

    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        search_service: VectorSearchService | None = None,
    ):
        self.embedding_service = embedding_service or EmbeddingService()
        self.search_service = search_service or VectorSearchService()

    def search(
        self,
        db: Session,
        *,
        user_id: int,
        question: str,
        document_ids: list[int] | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        query_embedding = self.embedding_service.generate_embedding(question)
        return self.search_service.search(
            db,
            user_id=user_id,
            query_embedding=query_embedding,
            document_ids=document_ids,
            top_k=settings.TOP_K if top_k is None else top_k,
        )
