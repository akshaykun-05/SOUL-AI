"""Stable JSON envelope for calls from a separate backend."""

from __future__ import annotations

import base64
import binascii
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soul_ai.schemas import AtomicClaim
from soul_ai.verification import ClaimVerificationResult


class ResultStatus(str, Enum):
    success = "success"
    error = "error"


class EngineError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False


T = TypeVar("T")


class EngineResult(BaseModel, Generic[T]):
    """All public service methods return this envelope (data OR error)."""
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    status: ResultStatus
    data: T | None = None
    error: EngineError | None = None

    @model_validator(mode="after")
    def valid_result_shape(self):
        if (self.status == ResultStatus.success) != (self.data is not None and self.error is None):
            raise ValueError("success requires data and no error")
        if (self.status == ResultStatus.error) != (self.error is not None and self.data is None):
            raise ValueError("error requires error detail and no data")
        return self


class PdfUpload(BaseModel):
    """JSON upload format; ``content_base64`` holds the original PDF bytes."""
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)

    @field_validator("content_base64")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 must contain valid base64") from exc
        return value

    def bytes(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class EvidenceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    document_ids: list[str] | None = None
    limit: int = Field(default=20, ge=1, le=500)


class ClaimsRequest(EvidenceQuery):
    evidence_ids: list[str] | None = None


class VerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    claims: list[AtomicClaim]
    evidence_ids: list[str] | None = None


class ContradictionRequest(VerificationRequest):
    verifications: list[ClaimVerificationResult]
    document_versions: dict[str, str] | None = None


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    input_path: str = Field(min_length=1)
    labels_path: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
