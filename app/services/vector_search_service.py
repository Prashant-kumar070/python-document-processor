import math
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models.chunk import DocumentChunk
from app.db.models.document import Document
from app.services.embedding_service import VECTOR_DIMENSIONS


class VectorSearchService:
    def search(
        self,
        db: Session,
        *,
        user_id: int,
        query_embedding: list[float],
        document_ids: list[int] | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_query(query_embedding)
        limit = settings.TOP_K if top_k is None else top_k
        if limit <= 0:
            raise ValueError("top_k must be greater than zero")

        if document_ids is not None:
            if not document_ids:
                return []
            self._validate_document_ownership(db, user_id, document_ids)

        if settings.DATABASE_URL.startswith("sqlite"):
            return self._search_sqlite(
                db,
                user_id=user_id,
                query_embedding=query_embedding,
                document_ids=document_ids,
                top_k=limit,
            )

        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        query = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id,
                DocumentChunk.content.label("content"),
                DocumentChunk.page_number,
                Document.original_filename.label("filename"),
                distance.label("distance"),
                (1 - distance).label("similarity_score"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.user_id == user_id)
        )
        if document_ids is not None:
            query = query.where(Document.id.in_(document_ids))
        query = query.order_by(distance).limit(limit)
        result = db.execute(query).mappings().all()
        return [
            {
                **dict(row),
                "chunk_content": row["content"],
            }
            for row in result
        ]

    @staticmethod
    def _validate_query(query_embedding: list[float]) -> None:
        if len(query_embedding) != VECTOR_DIMENSIONS:
            raise ValueError(
                f"Expected a query embedding with {VECTOR_DIMENSIONS} dimensions; "
                f"received {len(query_embedding)}."
            )
        if any(not math.isfinite(value) for value in query_embedding):
            raise ValueError("Query embedding contains a non-finite value.")

    @staticmethod
    def _validate_document_ownership(
        db: Session, user_id: int, document_ids: list[int]
    ) -> None:
        requested_ids = set(document_ids)
        owned_ids = {
            document_id
            for (document_id,) in db.query(Document.id)
            .filter(
                Document.user_id == user_id,
                Document.id.in_(requested_ids),
            )
            .all()
        }
        if owned_ids != requested_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more documents were not found.",
            )

    def _search_sqlite(
        self,
        db: Session,
        *,
        user_id: int,
        query_embedding: list[float],
        document_ids: list[int] | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        query = (
            db.query(DocumentChunk)
            .join(Document)
            .filter(Document.user_id == user_id)
        )
        if document_ids is not None:
            query = query.filter(Document.id.in_(document_ids))

        results = []
        for chunk in query.all():
            similarity = self.cosine_similarity(query_embedding, chunk.embedding)
            results.append(
                {
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "filename": chunk.document.original_filename,
                    "page_number": chunk.page_number,
                    "content": chunk.content,
                    "chunk_content": chunk.content,
                    "similarity_score": similarity,
                    "distance": 1 - similarity,
                }
            )
        return sorted(
            results,
            key=lambda item: item["similarity_score"],
            reverse=True,
        )[:top_k]

    @staticmethod
    def cosine_similarity(
        vec_a: list[float], vec_b: list[float] | None
    ) -> float:
        if not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(value * value for value in vec_a))
        norm_b = math.sqrt(sum(value * value for value in vec_b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)
