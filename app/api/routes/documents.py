from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DocumentService(db)
    document = service.save_upload(user_id=current_user.id, uploaded_file=file)
    background_tasks.add_task(service.process_document, document.id, current_user.id)
    return {
        "id": document.id,
        "filename": document.filename,
        "original_filename": document.original_filename,
        "status": document.status,
        "message": "Document uploaded and queued for processing",
    }


@router.get("")
def list_documents(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = DocumentService(db)
    documents = service.get_user_documents(current_user.id)
    return [
        {
            "id": document.id,
            "filename": document.original_filename,
            "file_type": document.file_type,
            "status": document.status,
            "processing_error": document.processing_error,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "processed_at": document.processed_at.isoformat() if document.processed_at else None,
            "chunk_count": len(document.chunks),
        }
        for document in documents
    ]


@router.get("/{document_id}")
def get_document(document_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = DocumentService(db)
    document = service.get_document(document_id, current_user.id)
    return {
        "id": document.id,
        "filename": document.original_filename,
        "file_type": document.file_type,
        "status": document.status,
        "processing_error": document.processing_error,
        "created_at": document.created_at.isoformat() if document.created_at else None,
        "processed_at": document.processed_at.isoformat() if document.processed_at else None,
        "chunk_count": len(document.chunks),
    }


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service = DocumentService(db)
    service.delete_document(document_id, current_user.id)
    return None
