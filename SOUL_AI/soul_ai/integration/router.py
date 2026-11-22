"""Optional FastAPI router; the owning backend can mount it or call the service directly."""
from __future__ import annotations

from fastapi import APIRouter
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


def create_router(service: SOULAIEngineService) -> APIRouter:
    router = APIRouter(prefix="/soul-ai", tags=["SOUL AI"])

    @router.post("/documents/extract")
    def extract(request: ExtractionRequest):
        return service.extract_documents(request.request_id, request.documents).model_dump(mode="json")

    @router.post("/answer")
    async def answer(request: AnswerRequest):
        result = await service.answer(request.request_id, request.question,
                                      request.document_ids, request.document_versions)
        return result.model_dump(mode="json")

    return router
