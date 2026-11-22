from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

from soul_ai.schemas import (
    AtomicClaim,
    Contradiction,
    DocumentMetadata,
    VerificationResult,
)


class ResponseStatus(str, Enum):
    completed = "completed"
    rejected = "rejected"
    error = "error"


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    document_ids: list[str] = Field(default_factory=list)


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class VerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    status: ResponseStatus
    answer: str | None = None
    documents: list[DocumentMetadata] = Field(default_factory=list)
    claims: list[AtomicClaim] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)
    contradictions: list[Contradiction] = Field(default_factory=list)
    rejected_reason: str | None = None
    error: ErrorDetail | None = None
