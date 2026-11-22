from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    document_ids: list[UUID] = Field(min_length=1, max_length=5)

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, ids: list[UUID]) -> list[UUID]:
        if len(set(ids)) != len(ids):
            raise ValueError("Document IDs must be unique.")
        return ids


class AnswerCitation(BaseModel):
    document_id: UUID
    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    quote: str = Field(min_length=1)


class QuestionAnswer(BaseModel):
    status: Literal["answered", "refused"]
    question: str
    answer: str | None = None
    citations: list[AnswerCitation] = Field(default_factory=list)
    refusal_reason: Literal[
        "insufficient_evidence", "ocr_required", "conflicting_evidence", "evidence_limit",
    ] | None = None
    warnings: list[str] = Field(default_factory=list)
