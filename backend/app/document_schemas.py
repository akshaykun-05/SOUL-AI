from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


ExtractionStatus = Literal["extracted", "ocr_required"]


class DocumentPage(BaseModel):
    page_number: int
    text: str
    character_count: int
    has_text: bool


class DocumentResponse(BaseModel):
    document_id: UUID
    filename: str
    page_count: int
    total_characters: int
    extraction_status: ExtractionStatus
    ocr_required: bool
    pages: list[DocumentPage]


class DocumentSummary(BaseModel):
    document_id: UUID
    filename: str
    file_size_bytes: int
    mime_type: str
    page_count: int
    total_characters: int
    extraction_status: ExtractionStatus
    ocr_required: bool
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentSummary]
    total: int
    limit: int
    offset: int


class UploadSuccess(BaseModel):
    status: Literal["success"] = "success"
    filename: str
    document: DocumentResponse


class UploadFailure(BaseModel):
    status: Literal["error"] = "error"
    filename: str
    status_code: int
    detail: str


class BatchUploadResponse(BaseModel):
    results: list[UploadSuccess | UploadFailure]
