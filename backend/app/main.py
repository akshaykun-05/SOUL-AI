import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings
from .database import Database
from .documents import create_documents_router


logger = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    service: str
    version: str
    status: Literal["healthy"] = "healthy"


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()

    database = Database(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            database.initialize()
            yield
        finally:
            database.dispose()

    app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
    app.state.database = database

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("Document database operation failed", exc_info=exc)
        return JSONResponse(
            status_code=500, content={"detail": "Document database operation failed."}
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["Health"])
    def health() -> HealthResponse:
        return HealthResponse(service=settings.service_name, version=settings.version)

    app.include_router(create_documents_router(settings, database))
    return app


app = create_app()
