"""
Knowledge base for RAG: uploaded company documents split into chunks.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.lead import iso


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, index=True)
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    chars: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing | ready | failed
    embedded: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id, "agent_id": self.agent_id, "title": self.title, "filename": self.filename,
            "content_type": self.content_type, "size_bytes": self.size_bytes, "chars": self.chars,
            "chunk_count": self.chunk_count, "status": self.status, "embedded": self.embedded,
            "error": self.error, "created_at": iso(self.created_at),
        }


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # float32 little-endian vector (None when embeddings are unavailable)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
