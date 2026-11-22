"""Optional FastAPI router; the owning backend can mount it or call the service directly."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from soul_ai.integration.contracts import PdfUpload
from soul_ai.integration.service import SOULAIEngineService


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    document_ids: list[str] | None = None
    document_versions: dict[str, str] | None = None


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    documents: list[PdfUpload] = Field(min_length=1)


def create_router(
    service: SOULAIEngineService,
    *,
    max_upload_bytes: int = 25 * 1024 * 1024,
    max_documents: int = 10,
) -> APIRouter:
    router = APIRouter(prefix="/soul-ai", tags=["SOUL AI"])

    @router.post("/documents/extract")
    def extract(request: ExtractionRequest):
        if len(request.documents) > max_documents:
            raise HTTPException(status_code=413, detail=f"A maximum of {max_documents} PDFs can be uploaded at once.")
        if any(len(item.bytes()) > max_upload_bytes for item in request.documents):
            raise HTTPException(status_code=413, detail=f"Each PDF must be at most {max_upload_bytes // (1024 * 1024)} MB.")
        return service.extract_documents(request.request_id, request.documents).model_dump(mode="json")

    @router.post("/answer")
    async def answer(request: AnswerRequest):
        if request.document_ids is not None and not request.document_ids:
            raise HTTPException(status_code=400, detail="Upload at least one PDF before asking a question.")
        result = await service.answer(request.request_id, request.question,
                                      request.document_ids, request.document_versions)
        if result.status.value == "error":
            status_code = 503 if result.error and result.error.code == "PROVIDER_NOT_CONFIGURED" else 400
            raise HTTPException(status_code=status_code, detail=result.error.message if result.error else "AI request failed.")
        return result.model_dump(mode="json")

    @router.get("/documents/{document_id}/file")
    def document_file(document_id: str):
        stored = service.get_document_pdf(document_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="The requested PDF is not in this backend session.")
        filename, content = stored
        safe_filename = quote(filename.replace("\r", "").replace("\n", ""))
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{safe_filename}"},
        )

    return router
