from enum import Enum
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ClaimType(str, Enum):
    factual = "factual"
    numerical = "numerical"
    date = "date"
    identity = "identity"
    relational = "relational"
    other = "other"


class AtomicClaim(BaseModel):
    """One candidate fact, awaiting independent verification in Phase 3."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(
        min_length=1,
        validation_alias=AliasChoices("claim_text", "text"),
    )
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    temporal_context: str | None = None
    source_evidence_ids: list[str] = Field(default_factory=list)
    # Retained for compatibility with Phase 0 callers; extraction returns claim_text.
    claim_type: ClaimType = Field(default=ClaimType.factual, exclude=True)
    source_document_ids: list[str] = Field(default_factory=list, exclude=True)

    @property
    def text(self) -> str:
        """Compatibility accessor for earlier callers."""
        return self.claim_text
