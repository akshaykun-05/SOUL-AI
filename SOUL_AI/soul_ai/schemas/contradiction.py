from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class ContradictionSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Contradiction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contradiction_id: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=2)
    evidence_ids: list[str] = Field(min_length=2)
    description: str = Field(min_length=1)
    severity: ContradictionSeverity
