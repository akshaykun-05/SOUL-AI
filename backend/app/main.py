from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import Settings


class HealthResponse(BaseModel):
    service: str
    version: str
    status: Literal["healthy"] = "healthy"


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    app = FastAPI(title=settings.service_name, version=settings.version)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["Health"])
    def health() -> HealthResponse:
        return HealthResponse(service=settings.service_name, version=settings.version)

    return app


app = create_app()
