from sqlalchemy.orm import Session

from app.db.models.chunk import DocumentChunk


class ChunkRepository:
    def __init__(self, db: Session):
        self.db = db

    def delete_document_chunks(self, document_id: int) -> None:
        try:
            self.db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def save_chunks(self, document_id: int, chunks: list[dict]) -> list[DocumentChunk]:
        try:
            saved = self._make_chunks(document_id, chunks)
            self.db.add_all(saved)
            self.db.commit()
            return saved
        except Exception:
            self.db.rollback()
            raise

    def replace_document_chunks(
        self, document_id: int, chunks: list[dict]
    ) -> list[DocumentChunk]:
        try:
            self.db.query(DocumentChunk).filter(
                DocumentChunk.document_id == document_id
            ).delete(synchronize_session=False)
            saved = self._make_chunks(document_id, chunks)
            self.db.add_all(saved)
            self.db.commit()
            return saved
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _make_chunks(document_id: int, chunks: list[dict]) -> list[DocumentChunk]:
        return [
            DocumentChunk(
                document_id=document_id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                embedding=chunk["embedding"],
                page_number=chunk.get("page_number"),
            )
            for chunk in chunks
        ]
