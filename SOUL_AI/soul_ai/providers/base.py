from typing import Protocol

from pydantic import BaseModel, ConfigDict


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_prompt: str
    temperature: float = 0.0


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    provider: str
    model: str


class LLMProvider(Protocol):
    """Provider-independent interface for future LLM integrations."""

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a model response for the supplied prompts."""
        ...
