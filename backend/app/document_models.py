from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(200))
    stored_filename: Mapped[str] = mapped_column(String(80), unique=True)
    file_size_bytes: Mapped[int]
    mime_type: Mapped[str] = mapped_column(String(100))
    page_count: Mapped[int]
    total_characters: Mapped[int]
    extraction_status: Mapped[str] = mapped_column(String(30), index=True)
    ocr_required: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        order_by="DocumentPage.page_number",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True
    )
    page_number: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    character_count: Mapped[int]
    has_text: Mapped[bool]

    document: Mapped[Document] = relationship(back_populates="pages")


class AIResultCache(Base):
    __tablename__ = "ai_result_cache"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True
    )
    cache_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)


class ContradictionAnalysis(Base):
    __tablename__ = "contradiction_analyses"

    analysis_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


class ContradictionSource(Base):
    __tablename__ = "contradiction_sources"

    analysis_id: Mapped[UUID] = mapped_column(
        ForeignKey("contradiction_analyses.analysis_id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))


class VerifiedInputCache(Base):
    __tablename__ = "verified_comparison_inputs"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.document_id", ondelete="CASCADE"), primary_key=True
    )
    cache_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
