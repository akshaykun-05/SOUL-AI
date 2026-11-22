from enum import Enum
from pydantic import BaseModel, ConfigDict, Field


class ClaimType(str, Enum):
    factual = "factual"
    numerical = "numerical"
    date = "date"
    identity = "identity"
    relational = "relational"
    other = "other"


class AtomicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    claim_type: ClaimType = ClaimType.factual
    source_document_ids: list[str] = Field(default_factory=list)
