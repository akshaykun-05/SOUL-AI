import logging
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from python_multipart.exceptions import MultipartParseError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .config import BACKEND_DIR, Settings
from .database import Database, get_session
from .document_models import Document
from .document_repository import delete_document, get_document, list_documents
from .document_schemas import (
    DocumentList,
    ExtractionStatus,
    BatchUploadResponse,
    DocumentResponse,
    UploadFailure,
    UploadSuccess,
)
from .document_storage import DocumentStorage
from .pdf_ingestion import CHUNK_SIZE, PDFIngestion, UploadError, display_filename

logger = logging.getLogger(__name__)
MULTIPART_OVERHEAD = 1024 * 1024
DocumentSession = Annotated[Session, Depends(get_session)]


class BodyTooLarge(MultiPartException):
    pass


class UploadParser(MultiPartParser):
    complete = False

    def on_end(self) -> None:
        self.complete = True


async def bounded_body(request: Request, limit: int):
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge("The multipart request exceeds the upload size limit.")
        for offset in range(0, len(chunk), CHUNK_SIZE):
            yield chunk[offset:offset + CHUNK_SIZE]


@asynccontextmanager
async def uploaded_files(request: Request, field: str, max_files: int, max_bytes: int):
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Expected multipart/form-data.")
    parser = UploadParser(
        request.headers,
        bounded_body(request, max_files * max_bytes + MULTIPART_OVERHEAD),
        max_files=max_files,
        max_fields=0,
    )
    try:
        form = await parser.parse()
    except BodyTooLarge as exc:
        raise HTTPException(413, exc.message) from exc
    except MultiPartException as exc:
        raise HTTPException(400, exc.message) from exc
    except MultipartParseError as exc:
        raise HTTPException(400, "Invalid multipart data.") from exc

    try:
        if not parser.complete:
            raise HTTPException(400, "Incomplete multipart data.")
        files = form.getlist(field)
        if not files or any(key != field for key in form):
            raise HTTPException(422, f"Provide files in the '{field}' multipart field.")
        if any(not isinstance(file, UploadFile) for file in files):
            raise HTTPException(422, f"The '{field}' field must contain files.")
        yield files
    finally:
        await form.close()


def multipart_schema(field: str, max_files: int | None = None) -> dict:
    binary = {"type": "string", "format": "binary"}
    field_schema = binary if max_files is None else {
        "type": "array", "items": binary, "minItems": 1, "maxItems": max_files
    }
    return {"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": [field], "properties": {field: field_schema}}
    }}}}


def create_documents_router(settings: Settings, database: Database) -> APIRouter:
    router = APIRouter(prefix="/api/documents", tags=["Documents"])
    storage = DocumentStorage(BACKEND_DIR / settings.upload_dir)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    ingestion = PDFIngestion(storage, max_bytes, database.sessions)

    async def ingest_file(file: UploadFile, filename: str) -> DocumentResponse:
        if file.size is not None and file.size > max_bytes:
            raise UploadError(413, "The PDF exceeds the configured upload size.")
        try:
            return await ingestion.ingest(file.file, filename)
        except (OSError, SQLAlchemyError) as exc:
            logger.exception("Could not store uploaded PDF")
            raise UploadError(500, "Could not store the uploaded PDF.") from exc

    @router.post("/upload", status_code=201, openapi_extra=multipart_schema("file"))
    async def upload(request: Request) -> DocumentResponse:
        async with uploaded_files(request, "file", 1, max_bytes) as files:
            file = files[0]
            try:
                return await ingest_file(file, display_filename(file.filename))
            except UploadError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post(
        "/upload-multiple",
        openapi_extra=multipart_schema("files", settings.max_files_per_upload),
    )
    async def upload_multiple(request: Request) -> BatchUploadResponse:
        results = []
        async with uploaded_files(
            request, "files", settings.max_files_per_upload, max_bytes
        ) as files:
            for file in files:
                filename = display_filename(file.filename)
                try:
                    document = await ingest_file(file, filename)
                except UploadError as exc:
                    results.append(UploadFailure(
                        filename=filename, status_code=exc.status_code, detail=str(exc)
                    ))
                else:
                    results.append(UploadSuccess(filename=filename, document=document))
        return BatchUploadResponse(results=results)

    @router.get("")
    def list_route(
        session: DocumentSession,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
        extraction_status: ExtractionStatus | None = None,
        search: Annotated[str | None, Query(max_length=200)] = None,
    ) -> DocumentList:
        return list_documents(session, limit, offset, extraction_status, search)

    @router.get("/{document_id}")
    def get_route(document_id: UUID, session: DocumentSession) -> DocumentResponse:
        try:
            return get_document(session, document_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Document not found.") from exc

    @router.get(
        "/{document_id}/file",
        response_class=FileResponse,
        responses={200: {"content": {"application/pdf": {
            "schema": {"type": "string", "format": "binary"}
        }}}},
    )
    def get_document_file(document_id: UUID, session: DocumentSession) -> FileResponse:
        document = session.get(Document, document_id)
        if document is None:
            raise HTTPException(404, "Document not found.")
        try:
            path = storage.pdf_path(document_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Document not found.") from exc
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=document.original_filename,
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @router.delete("/{document_id}", status_code=204)
    def delete_route(document_id: UUID, session: DocumentSession) -> Response:
        try:
            delete_document(session, storage, document_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Document not found.") from exc
        except OSError as exc:
            logger.exception("Could not delete stored PDF")
            raise HTTPException(500, "Could not delete the document.") from exc
        return Response(status_code=204)

    return router
