from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_current_user
from app.db.database import get_db
from app.db.models.user import User
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.embedding_service import EmbeddingProviderError
from app.services.llm_service import LLMProviderError
from app.services.rag_service import RagService

router = APIRouter(tags=["Chat"])


@router.post("/chat", response_model=ChatResponse)
def ask_question(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rag = RagService(db)
    try:
        return rag.answer_question(
            user_id=current_user.id,
            question=payload.question,
            conversation_id=payload.conversation_id,
            document_ids=payload.document_ids,
        )
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.response_detail(),
        ) from exc
    except EmbeddingProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.get("/conversations")
def list_conversations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = ConversationRepository(db)
    conversations = repo.list_for_user(current_user.id)
    return [{
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "message_count": len(conversation.messages),
    } for conversation in conversations]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = ConversationRepository(db)
    conversation = repo.get_for_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
        "messages": [{
            "id": message.id,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at.isoformat() if message.created_at else None,
            "sources": [{
                "document_chunk_id": source.document_chunk_id,
                "similarity_score": source.similarity_score,
            } for source in message.sources],
        } for message in conversation.messages],
    }


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = ConversationRepository(db)
    conversation = repo.get_for_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    repo.delete(conversation)
    return None
