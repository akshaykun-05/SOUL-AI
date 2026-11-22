"""Standard-library HTTP adapter for OpenAI-compatible chat-completions APIs."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from soul_ai.config import LLMSettings
from soul_ai.providers.base import LLMRequest, LLMResponse


class ProviderConfigurationError(ValueError):
    """Raised when a provider cannot be configured from the environment."""


class ProviderRequestError(RuntimeError):
    """Raised when a model endpoint rejects or fails a request."""


class OpenAICompatibleProvider:
    """Call a configurable OpenAI-compatible ``/chat/completions`` endpoint.

    Uses the Python standard library; no provider SDK is required. The API key
    is read only from ``SOUL_LLM_API_KEY``/``.env`` via ``LLMSettings``.
    """

    def __init__(self, settings: LLMSettings | None = None, *, timeout_seconds: float = 60.0):
        self.settings = settings or LLMSettings()
        self.timeout_seconds = timeout_seconds
        self.base_url = (self.settings.base_url or "https://api.openai.com/v1").rstrip("/")
        if not self.settings.api_key or not self.settings.api_key.get_secret_value().strip():
            raise ProviderConfigurationError("SOUL_LLM_API_KEY is required for the configured HTTP provider")

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return await asyncio.to_thread(self._generate_sync, request)

    def _generate_sync(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.settings.model,
            "temperature": request.temperature,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        }
        http_request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderRequestError(f"Model endpoint returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderRequestError("Could not reach the configured model endpoint") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderRequestError("Model endpoint returned invalid JSON") from exc

        try:
            text = body["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
            if not isinstance(text, str) or not text.strip():
                raise TypeError("empty response content")
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderRequestError("Model endpoint response is missing chat content") from exc
        return LLMResponse(text=text, provider=self.settings.provider, model=self.settings.model)
