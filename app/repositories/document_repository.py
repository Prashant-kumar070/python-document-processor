from typing import Optional

from sqlalchemy.orm import Session

from app.db.models.document import Document


class DocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, user_id: int, filename: str, original_filename: str, file_type: str) -> Document:
        document = Document(
            user_id=user_id,
            filename=filename,
            original_filename=original_filename,
            file_type=file_type,
            status="PENDING",
        )
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def get_by_id(self, document_id: int, user_id: Optional[int] = None) -> Optional[Document]:
        query = self.db.query(Document).filter(Document.id == document_id)
        if user_id is not None:
            query = query.filter(Document.user_id == user_id)
        return query.first()

    def list_for_user(self, user_id: int) -> list[Document]:
        return self.db.query(Document).filter(Document.user_id == user_id).order_by(Document.created_at.desc()).all()

    def update_status(self, document: Document, *, status: str, processing_error: Optional[str] = None) -> Document:
        document.status = status
        if processing_error is not None:
            document.processing_error = processing_error
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def delete(self, document: Document) -> None:
        self.db.delete(document)
        self.db.commit()
