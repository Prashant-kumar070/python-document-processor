from sqlalchemy.orm import Session

from app.db.models.conversation import Conversation
from app.db.models.message import Message
from app.db.models.message_source import MessageSource


class ConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, *, user_id: int, title: str) -> Conversation:
        conversation = Conversation(user_id=user_id, title=title)
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def list_for_user(self, user_id: int) -> list[Conversation]:
        return self.db.query(Conversation).filter(Conversation.user_id == user_id).order_by(Conversation.created_at.desc()).all()

    def get_for_user(self, conversation_id: int, user_id: int) -> Conversation | None:
        return self.db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.user_id == user_id).first()

    def get_recent_messages(self, conversation_id: int, limit: int) -> list[Message]:
        if limit <= 0:
            return []
        messages = (
            self.db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(messages))

    def save_exchange(
        self,
        *,
        user_id: int,
        conversation_id: int | None,
        title: str,
        question: str,
        answer: str,
        source_entries: list[dict],
    ) -> Conversation:
        try:
            if conversation_id is None:
                conversation = Conversation(user_id=user_id, title=title)
                self.db.add(conversation)
                self.db.flush()
            else:
                conversation = self.get_for_user(conversation_id, user_id)
                if conversation is None:
                    raise LookupError("Conversation not found.")

            user_message = Message(
                conversation_id=conversation.id,
                role="user",
                content=question,
            )
            assistant_message = Message(
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
            )
            self.db.add_all([user_message, assistant_message])
            self.db.flush()
            self.db.add_all(
                [
                    MessageSource(
                        message_id=assistant_message.id,
                        document_chunk_id=entry["document_chunk_id"],
                        similarity_score=entry["similarity_score"],
                    )
                    for entry in source_entries
                ]
            )
            self.db.commit()
            self.db.refresh(conversation)
            return conversation
        except Exception:
            self.db.rollback()
            raise

    def delete(self, conversation: Conversation) -> None:
        self.db.delete(conversation)
        self.db.commit()

    def add_message(self, *, conversation_id: int, role: str, content: str) -> Message:
        message = Message(conversation_id=conversation_id, role=role, content=content)
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def add_sources(self, *, message_id: int, source_entries: list[dict]) -> None:
        for entry in source_entries:
            self.db.add(
                MessageSource(
                    message_id=message_id,
                    document_chunk_id=entry["document_chunk_id"],
                    similarity_score=entry["similarity_score"],
                )
            )
        self.db.commit()
