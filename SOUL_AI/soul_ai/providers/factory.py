"""Environment-backed default provider construction."""

from soul_ai.config import LLMSettings
from soul_ai.providers.base import LLMProvider
from soul_ai.providers.openai_compatible import (
    OpenAICompatibleProvider,
    ProviderConfigurationError,
)


def create_configured_provider(settings: LLMSettings | None = None) -> LLMProvider:
    settings = settings or LLMSettings()
    if settings.provider.casefold() not in {"openai", "openai-compatible"}:
        raise ProviderConfigurationError(
            f"No built-in adapter for provider {settings.provider!r}; inject an LLMProvider implementation."
        )
    return OpenAICompatibleProvider(settings)
