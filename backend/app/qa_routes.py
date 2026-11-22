import asyncio
import logging

from fastapi import APIRouter, HTTPException
from starlette.concurrency import run_in_threadpool

from . import ai_adapter, qa_service
from .contradiction_service import load_sources, source_identity
from .database import Database
from .qa_schemas import QuestionAnswer, QuestionRequest

logger = logging.getLogger(__name__)


def create_qa_router(database: Database) -> APIRouter:
    router = APIRouter(prefix="/api/ai", tags=["AI"])

    @router.post(
        "/ask",
        description="Answer a question from selected PDFs, or refuse when verified evidence is insufficient.",
        responses={
            404: {"description": "Source document not found"},
            409: {"description": "Source document changed during processing"},
            503: {"description": "Engine or model provider unavailable"},
            504: {"description": "Question answering timed out"},
        },
    )
    async def ask(request: QuestionRequest) -> QuestionAnswer:
        try:
            documents = await run_in_threadpool(load_sources, database, request.document_ids)
            if not qa_service.readable_documents(documents):
                return QuestionAnswer(
                    status="refused", question=request.question, refusal_reason="ocr_required",
                    warnings=["No selected document contains readable text. OCR is required."],
                )
            readiness, engine = await run_in_threadpool(ai_adapter.inspect_engine)
            if engine is None:
                raise ai_adapter.EngineUnavailable(readiness.diagnostics[0])
            result = await asyncio.wait_for(
                qa_service.answer_question(request.question, documents, engine),
                timeout=qa_service.QUESTION_TIMEOUT,
            )
            if result.status == "answered":
                current = await run_in_threadpool(load_sources, database, request.document_ids)
                if any(source_identity(old) != source_identity(new) for old, new in zip(documents, current)):
                    raise HTTPException(409, "Source documents changed during question answering.")
            return result
        except FileNotFoundError as exc:
            raise HTTPException(404, "One or more documents were not found.") from exc
        except (ai_adapter.EngineUnavailable, ImportError) as exc:
            detail = str(exc) if isinstance(exc, ai_adapter.EngineUnavailable) else "A question-answering dependency is unavailable."
            raise HTTPException(503, detail) from exc
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise HTTPException(504, "Question answering timed out.") from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Question answering failed (%s)", type(exc).__name__)
            raise HTTPException(500, "Question answering failed.") from exc

    return router
