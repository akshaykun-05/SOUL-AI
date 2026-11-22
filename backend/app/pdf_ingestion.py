import asyncio
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import BinaryIO
from uuid import uuid4

import pymupdf
from sqlalchemy.orm import Session, sessionmaker

from .document_repository import persist_upload
from .document_schemas import DocumentPage, DocumentResponse
from .document_storage import DocumentStorage

CHUNK_SIZE = 64 * 1024
# All application calls into MuPDF run on the same worker thread.
_pdf_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-ingestion")


class UploadError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code


def display_filename(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(
        char for char in name
        if not unicodedata.category(char).startswith("C")
    ).strip(" .")
    if len(name) > 200:
        stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
        name = stem[:195] + "." + suffix if suffix.lower() == "pdf" else name[:200]
    return name or "unnamed"


class PDFIngestion:
    def __init__(
        self, storage: DocumentStorage, max_bytes: int, sessions: sessionmaker[Session]
    ):
        self.sessions = sessions
        self.storage = storage
        self.max_bytes = max_bytes

    async def ingest(self, source: BinaryIO, filename: str) -> DocumentResponse:
        return await asyncio.get_running_loop().run_in_executor(
            _pdf_worker, self._ingest, source, filename
        )

    def _ingest(self, source: BinaryIO, filename: str) -> DocumentResponse:
        first_chunk = source.read(CHUNK_SIZE)
        if not first_chunk:
            raise UploadError(422, "The uploaded file is empty.")
        if not filename.lower().endswith(".pdf") or not first_chunk.startswith(b"%PDF-"):
            raise UploadError(415, "Upload a PDF file with a valid PDF header.")

        with self.storage.stage() as staging:
            pdf_path = staging / self.storage.PDF_NAME
            pdf_bytes = bytearray()
            with pdf_path.open("xb") as output:
                chunk = first_chunk
                while chunk:
                    if len(pdf_bytes) + len(chunk) > self.max_bytes:
                        raise UploadError(413, "The PDF exceeds the configured upload size.")
                    output.write(chunk)
                    pdf_bytes.extend(chunk)
                    chunk = source.read(CHUNK_SIZE)

            pages = extract_pages(pdf_bytes)
            ocr_required = not any(page.has_text for page in pages)
            document = DocumentResponse(
                document_id=uuid4(),
                filename=filename,
                page_count=len(pages),
                total_characters=sum(page.character_count for page in pages),
                extraction_status="ocr_required" if ocr_required else "extracted",
                ocr_required=ocr_required,
                pages=pages,
            )
            with self.sessions() as session:
                persist_upload(session, self.storage, staging, document, len(pdf_bytes))
            return document


def extract_pages(data: bytearray) -> list[DocumentPage]:
    if not data[-1024:].rstrip(b"\x00\t\n\f\r ").endswith(b"%%EOF"):
        raise UploadError(422, "The PDF is missing its end-of-file marker.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            if pdf.needs_pass or pdf.xref_get_key(-1, "Encrypt")[0] != "null":
                raise UploadError(422, "Password-protected PDFs are not accepted.")
            if not pdf.is_pdf or pdf.is_repaired or pdf.page_count < 1:
                raise UploadError(422, "The PDF is corrupted or has no pages.")
            pages = []
            for number, page in enumerate(pdf, start=1):
                text = page.get_text()
                pages.append(DocumentPage(
                    page_number=number,
                    text=text,
                    character_count=len(text),
                    has_text=bool(text.strip()),
                ))
            return pages
    except (RuntimeError, ValueError) as exc:
        raise UploadError(422, "The PDF is corrupted or cannot be extracted.") from exc
