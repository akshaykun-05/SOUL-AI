import sys
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings


# The AI engine is an existing sibling Python package, not a separately hosted
# service. Make it importable for both `uvicorn backend.app.main:app` and
# `uvicorn app.main:app` when started from the backend directory.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = REPOSITORY_ROOT / "SOUL_AI"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))


class HealthResponse(BaseModel):
    service: str
    version: str
    status: Literal["healthy"] = "healthy"


def create_app(settings: Settings | None = None, engine_service=None) -> FastAPI:
    if settings is None:
        settings = Settings()

    app = FastAPI(title=settings.service_name, version=settings.version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["Health"])
    def health() -> HealthResponse:
        return HealthResponse(service=settings.service_name, version=settings.version)

    from soul_ai.integration.router import create_router
    from soul_ai.integration.service import SOULAIEngineService

    if engine_service is None:
        # A missing key must not prevent health checks or docs from starting.
        # The service reports PROVIDER_NOT_CONFIGURED for actual model requests.
        claim_provider = verifier_provider = None
        try:
            from soul_ai.config import LLMSettings
            from soul_ai.providers import create_configured_provider

            llm_settings = LLMSettings(_env_file=REPOSITORY_ROOT / "backend" / ".env")
            claim_provider = create_configured_provider(llm_settings)
            verifier_provider = create_configured_provider(llm_settings)
        except (ValueError, RuntimeError):
            pass
        engine_service = SOULAIEngineService(
            claim_provider=claim_provider,
            verifier_provider=verifier_provider,
        )

    app.state.engine_service = engine_service
    app.include_router(
        create_router(
            engine_service,
            max_upload_bytes=settings.max_upload_bytes,
            max_documents=settings.max_documents_per_request,
        ),
        prefix="/api",
    )

    @app.middleware("http")
    async def enforce_upload_limits(request, call_next):
        if request.method == "POST" and request.url.path == "/api/soul-ai/documents/extract":
            raw_length = request.headers.get("content-length")
            max_body = settings.max_upload_bytes * settings.max_documents_per_request * 4 // 3 + 1_000_000
            if raw_length and raw_length.isdigit() and int(raw_length) > max_body:
                return JSONResponse(status_code=413, content={"detail": "Upload request exceeds the configured size limit."})
        return await call_next(request)

    return app


app = create_app()
