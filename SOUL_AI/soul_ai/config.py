from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """Environment-backed model provider configuration."""

    model_config = SettingsConfigDict(
        env_prefix="SOUL_LLM_",
        env_file=".env",
        extra="ignore",
    )

    provider: str = "openai"
    model: str = "gpt-4.1-mini"
    api_key: SecretStr | None = None
    base_url: str | None = None
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
