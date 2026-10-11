from sqlalchemy import Column, Float, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.db.database import Base


class MessageSource(Base):
    __tablename__ = "message_sources"

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(Integer, ForeignKey("messages.id"), nullable=False, index=True)
    document_chunk_id = Column(Integer, ForeignKey("document_chunks.id"), nullable=False, index=True)
    similarity_score = Column(Float, nullable=False, default=0.0)

    message = relationship("Message", back_populates="sources")
    chunk = relationship("DocumentChunk")
