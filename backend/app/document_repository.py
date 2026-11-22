import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .document_models import (
    ContradictionAnalysis, ContradictionSource, Document, DocumentPage, utc_now,
)
from .document_schemas import DocumentList, DocumentPage as PageResponse
from .document_schemas import DocumentResponse, DocumentSummary, ExtractionStatus
from .document_storage import DocumentStorage

logger = logging.getLogger(__name__)


def add_document(
    session: Session,
    storage: DocumentStorage,
    document: DocumentResponse,
    file_size: int,
    timestamp: datetime | None = None,
) -> None:
    timestamp = timestamp or utc_now()
    session.add(Document(
        document_id=document.document_id,
        original_filename=document.filename,
        stored_filename=storage.stored_filename(document.document_id),
        file_size_bytes=file_size,
        mime_type="application/pdf",
        page_count=document.page_count,
        total_characters=document.total_characters,
        extraction_status=document.extraction_status,
        ocr_required=document.ocr_required,
        created_at=timestamp,
        updated_at=timestamp,
        pages=[DocumentPage(**page.model_dump()) for page in document.pages],
    ))


def persist_upload(
    session: Session,
    storage: DocumentStorage,
    staging: Path,
    document: DocumentResponse,
    file_size: int,
) -> None:
    published = None
    try:
        with session.begin():
            add_document(session, storage, document, file_size)
            session.flush()
            published = storage.publish(staging, document.document_id)
    except BaseException:
        if published is not None:
            storage.purge(published)
        raise


def get_document(session: Session, document_id: UUID) -> DocumentResponse:
    record = session.get(Document, document_id, options=[selectinload(Document.pages)])
    if record is None:
        raise FileNotFoundError
    return document_response(record)


def get_documents(session: Session, document_ids: list[UUID]) -> list[DocumentResponse]:
    statement = (
        select(Document).where(Document.document_id.in_(document_ids))
        .options(selectinload(Document.pages))
    )
    records = {record.document_id: record for record in session.scalars(statement)}
    if len(records) != len(document_ids):
        raise FileNotFoundError
    return [document_response(records[document_id]) for document_id in document_ids]


def document_response(record: Document) -> DocumentResponse:
    return DocumentResponse(
        document_id=record.document_id,
        filename=record.original_filename,
        page_count=record.page_count,
        total_characters=record.total_characters,
        extraction_status=record.extraction_status,
        ocr_required=record.ocr_required,
        pages=[PageResponse.model_validate(page, from_attributes=True) for page in record.pages],
    )


def list_documents(
    session: Session,
    limit: int,
    offset: int,
    extraction_status: ExtractionStatus | None,
    search: str | None,
) -> DocumentList:
    filters = []
    if extraction_status is not None:
        filters.append(Document.extraction_status == extraction_status)
    if search:
        filters.append(Document.original_filename.icontains(search, autoescape=True))

    total = session.scalar(select(func.count()).select_from(Document).where(*filters))
    statement = (
        select(Document)
        .where(*filters)
        .order_by(Document.created_at.desc(), Document.document_id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [
        DocumentSummary(
            document_id=record.document_id,
            filename=record.original_filename,
            file_size_bytes=record.file_size_bytes,
            mime_type=record.mime_type,
            page_count=record.page_count,
            total_characters=record.total_characters,
            extraction_status=record.extraction_status,
            ocr_required=record.ocr_required,
            created_at=record.created_at.replace(tzinfo=timezone.utc),
            updated_at=record.updated_at.replace(tzinfo=timezone.utc),
        )
        for record in session.scalars(statement)
    ]
    return DocumentList(items=items, total=total, limit=limit, offset=offset)


def delete_document(session: Session, storage: DocumentStorage, document_id: UUID) -> None:
    pending = None
    try:
        with session.begin():
            # Serialize writes before looking up the record or moving its files.
            session.connection(execution_options={"sqlite_immediate": True})
            record = session.get(Document, document_id)
            if record is None:
                raise FileNotFoundError
            pending = storage.quarantine(document_id)
            analysis_ids = select(ContradictionSource.analysis_id).where(
                ContradictionSource.document_id == document_id
            )
            session.execute(delete(ContradictionAnalysis).where(
                ContradictionAnalysis.analysis_id.in_(analysis_ids)
            ))
            session.delete(record)
    except BaseException:
        if pending is not None:
            storage.restore(pending, document_id)
        raise
    if pending is not None:
        try:
            storage.purge(pending)
        except OSError:
            logger.exception("Could not remove quarantined document %s", document_id)
