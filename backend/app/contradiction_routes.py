import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from . import ai_adapter
from .contradiction_schemas import AnalysisEvidence, ComparisonRequest, ComparisonResult, FindingEvidence
from .contradiction_service import (
    SourcesChanged, compare, load_sources, retrieve_analysis,
)
from .database import Database
from .document_models import utc_now

logger = logging.getLogger(__name__)


def create_contradiction_router(database: Database, cache_revision: str) -> APIRouter:
    router = APIRouter(prefix="/api/ai/contradictions", tags=["AI"])

    @router.post("", description="Compare two to five stored PDFs for independently verified contradictions.")
    async def analyze(request: ComparisonRequest) -> ComparisonResult:
        created_at = utc_now()
        try:
            documents = await run_in_threadpool(load_sources, database, request.document_ids)
        except FileNotFoundError as exc:
            raise HTTPException(404, "One or more documents were not found.") from exc
        try:
            readable = sum(
                not doc.ocr_required and any(page.text.strip() for page in doc.pages)
                for doc in documents
            )
            engine = None
            if readable >= 2:
                readiness, engine = await run_in_threadpool(ai_adapter.inspect_engine)
                if engine is None:
                    raise ai_adapter.EngineUnavailable(readiness.diagnostics[0])
            return await compare(database, documents, engine, cache_revision, created_at)
        except ai_adapter.EngineUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, "Contradiction analysis timed out.") from exc
        except SourcesChanged as exc:
            raise HTTPException(409, "Source documents changed during analysis.") from exc
        except Exception as exc:
            logger.error("Contradiction analysis failed (%s)", type(exc).__name__)
            raise HTTPException(500, "Contradiction analysis failed.") from exc

    @router.get("/{analysis_id}")
    def get_analysis(analysis_id: UUID) -> ComparisonResult:
        try:
            return retrieve_analysis(database, analysis_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Analysis not found.") from exc

    @router.get("/{analysis_id}/evidence")
    def get_evidence(analysis_id: UUID) -> AnalysisEvidence:
        result = get_analysis(analysis_id)
        return AnalysisEvidence(
            analysis_id=analysis_id,
            evidence=[
                FindingEvidence(
                    finding_id=finding.id, source_a=finding.claim_a.evidence,
                    source_b=finding.claim_b.evidence,
                )
                for finding in result.contradictions
            ],
        )

    return router
