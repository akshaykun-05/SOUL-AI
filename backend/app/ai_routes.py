import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from . import ai_adapter
from .ai_schemas import AIProcessingResult, AIStatus
from .ai_service import cache_key, load_document, read_cache, write_cache
from .database import Database

logger = logging.getLogger(__name__)


def create_ai_router(database: Database, cache_revision: str) -> APIRouter:
    router = APIRouter(prefix="/api/ai", tags=["AI"])

    @router.get("/status", description="Check engine imports and local provider configuration without sending a model request.")
    def status() -> AIStatus:
        try:
            result, _ = ai_adapter.inspect_engine()
            return result
        except Exception as exc:
            logger.error("AI readiness check failed (%s)", type(exc).__name__)
            return AIStatus(
                status="unavailable", package_ready=False, dependencies_ready=False,
                provider_configured=False, diagnostics=["The engine readiness check failed."],
            )

    @router.post(
        "/process/{document_id}",
        description="Process stored PDF text and return independently supported claims with page citations.",
        responses={404: {"description": "Document not found"}, 503: {"description": "Engine or provider unavailable"}},
    )
    async def process(document_id: UUID) -> AIProcessingResult:
        try:
            document = await run_in_threadpool(load_document, database, document_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Document not found.") from exc

        try:
            readiness, engine = await run_in_threadpool(ai_adapter.inspect_engine)
            if document.ocr_required or not any(page.text.strip() for page in document.pages):
                return AIProcessingResult(
                    document_id=document_id, processing_status="ocr_required",
                    engine_status=readiness.status, page_count=document.page_count,
                    unavailable_capabilities=[*ai_adapter.SCOPE_LIMITS, "ocr"],
                    warnings=["No readable text is stored. OCR is required before processing."],
                )
            if engine is None:
                raise ai_adapter.EngineUnavailable(readiness.diagnostics[0])
            key = cache_key(document, engine, cache_revision) if cache_revision else None
            if key:
                cached = await run_in_threadpool(read_cache, database, document, key)
                if cached is not None:
                    return cached

            result = await ai_adapter.process_document(document, engine)
            all_candidates_verified = ai_adapter.UNVERIFIED_WARNING not in result.warnings
            if key and result.claims and all_candidates_verified:
                await run_in_threadpool(write_cache, database, key, result)
            return result
        except ai_adapter.EngineUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except Exception as exc:
            logger.error("Document AI processing failed (%s)", type(exc).__name__)
            raise HTTPException(500, "AI processing failed.") from exc

    return router
