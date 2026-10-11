from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.services.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.utils.file_parser import parse_document_text


class DocumentService:
    def __init__(self, db: Session):
        self.db = db
        self.document_repository = DocumentRepository(db)
        self.chunk_repository = ChunkRepository(db)
        self.chunking_service = ChunkingService(chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP)
        self.embedding_service = EmbeddingService()

    def save_upload(self, *, user_id: int, uploaded_file: UploadFile) -> object:
        allowed_types = {"pdf": "pdf", "docx": "docx"}
        extension = uploaded_file.filename.rsplit(".", 1)[-1].lower() if uploaded_file.filename else ""
        if extension not in allowed_types:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported file type. Allowed types: pdf, docx")
        file_size = getattr(uploaded_file, "size", None)
        if file_size is not None and file_size > settings.MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds maximum allowed size")

        upload_dir = Path(settings.UPLOAD_DIR)
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = f"{user_id}_{datetime.now(timezone.utc).timestamp()}_{uploaded_file.filename}"
        file_path = upload_dir / safe_name
        contents = uploaded_file.file.read()
        file_path.write_bytes(contents)
        document = self.document_repository.create(
            user_id=user_id,
            filename=str(file_path.name),
            original_filename=uploaded_file.filename,
            file_type=allowed_types[extension],
        )
        return document

    def process_document(self, document_id: int, user_id: int) -> None:
        document = self.document_repository.get_by_id(document_id, user_id=user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        self.document_repository.update_status(document, status="PROCESSING")
        file_path = Path(settings.UPLOAD_DIR) / document.filename
        if not file_path.exists():
            self.document_repository.update_status(document, status="FAILED", processing_error="Uploaded file not found")
            return
        try:
            text_by_page = parse_document_text(str(file_path))
            chunks = self.chunking_service.chunk_pages(text_by_page)
            for chunk in chunks:
                chunk["embedding"] = None
            if not chunks:
                raise ValueError("No text could be extracted from the document")
            embeddings = self.embedding_service.generate_embeddings([chunk["content"] for chunk in chunks])
            for chunk, embedding in zip(chunks, embeddings):
                chunk["embedding"] = embedding
            self.chunk_repository.replace_document_chunks(document.id, chunks)
            document.processed_at = datetime.now(timezone.utc)
            self.document_repository.update_status(document, status="COMPLETED")
        except Exception as exc:
            self.document_repository.update_status(document, status="FAILED", processing_error=str(exc))
            raise

    def get_user_documents(self, user_id: int) -> list[object]:
        return self.document_repository.list_for_user(user_id)

    def get_document(self, document_id: int, user_id: int):
        document = self.document_repository.get_by_id(document_id, user_id=user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        return document

    def delete_document(self, document_id: int, user_id: int) -> None:
        document = self.document_repository.get_by_id(document_id, user_id=user_id)
        if not document:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
        file_path = Path(settings.UPLOAD_DIR) / document.filename
        if file_path.exists():
            file_path.unlink()
        self.chunk_repository.delete_document_chunks(document.id)
        self.document_repository.delete(document)

    def get_chunk_count(self, document) -> int:
        return len(document.chunks)
