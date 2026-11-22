from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EngineState = Literal["available", "unavailable"]


class AIStatus(BaseModel):
    status: EngineState
    capabilities: list[str] = Field(default_factory=list)
    package_ready: bool
    dependencies_ready: bool
    provider_configured: bool
    api_key_present: bool | None = Field(
        default=None, description="Whether a nonblank key was resolved, or null if settings could not be read."
    )
    diagnostics: list[str] = Field(default_factory=list)


class EvidenceReference(BaseModel):
    evidence_id: str = Field(min_length=1)
    document_id: UUID
    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class VerifiedClaim(BaseModel):
    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence_references: list[EvidenceReference] = Field(min_length=1)


class AIProcessingResult(BaseModel):
    document_id: UUID
    processing_status: Literal["completed", "insufficient_evidence", "ocr_required"]
    engine_status: EngineState
    page_count: int = Field(ge=1)
    claims: list[VerifiedClaim] | None = None
    evidence_references: list[EvidenceReference] | None = None
    unavailable_capabilities: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
