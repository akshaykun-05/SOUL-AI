from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOUL_BACKEND_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    service_name: str = "SOUL AI Backend"
    version: str = "0.1.0"
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    upload_dir: Path = Field(
        default=Path("uploads"),
        validation_alias=AliasChoices("SOUL_BACKEND_UPLOAD_DIR", "UPLOAD_DIR"),
    )
    max_upload_size_mb: int = Field(
        default=10,
        ge=1,
        validation_alias=AliasChoices(
            "SOUL_BACKEND_MAX_UPLOAD_SIZE_MB", "MAX_UPLOAD_SIZE_MB"
        ),
    )
    max_files_per_upload: int = Field(
        default=5,
        ge=1,
        le=5,
        validation_alias=AliasChoices(
            "SOUL_BACKEND_MAX_FILES_PER_UPLOAD", "MAX_FILES_PER_UPLOAD"
        ),
    )

    database_url: str = Field(
        default="sqlite:///data/soul_ai.sqlite3",
        validation_alias=AliasChoices("SOUL_BACKEND_DATABASE_URL", "DATABASE_URL"),
    )

    ai_cache_revision: str = ""
