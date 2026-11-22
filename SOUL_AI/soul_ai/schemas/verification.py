from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

from .evidence import ExtractedEvidence


class VerificationStatus(str, Enum):
    supported = "supported"
    contradicted = "contradicted"
    insufficient_evidence = "insufficient_evidence"
    indeterminate = "indeterminate"


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    status: VerificationStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[ExtractedEvidence] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
