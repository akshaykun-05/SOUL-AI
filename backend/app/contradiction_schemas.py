from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ai_schemas import EvidenceReference


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=2, max_length=5)

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, ids: list[UUID]) -> list[UUID]:
        if len(set(ids)) != len(ids):
            raise ValueError("Document IDs must be unique.")
        return ids


class ContradictionSide(BaseModel):
    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    value: str = Field(min_length=1)
    evidence: EvidenceReference


class VerifiedContradiction(BaseModel):
    id: str = Field(min_length=1)
    claim_a: ContradictionSide
    claim_b: ContradictionSide
    contradiction_type: Literal[
        "amount_conflict", "date_conflict", "interest_rate_conflict",
        "identity_conflict", "value_conflict",
    ]
    explanation: str = Field(min_length=1)
    confidence: None = None
    verification_status: Literal["verified"] = "verified"


class ComparisonResult(BaseModel):
    analysis_id: UUID
    status: Literal["completed", "insufficient_readable_documents"]
    document_ids: list[UUID]
    documents_analyzed: int
    ocr_required_document_ids: list[UUID]
    contradiction_count: int
    contradictions: list[VerifiedContradiction]
    warnings: list[str]
    created_at: datetime
    completed_at: datetime


class FindingEvidence(BaseModel):
    finding_id: str
    source_a: EvidenceReference
    source_b: EvidenceReference


class AnalysisEvidence(BaseModel):
    analysis_id: UUID
    evidence: list[FindingEvidence]
