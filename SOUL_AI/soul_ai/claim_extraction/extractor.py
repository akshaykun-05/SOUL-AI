"""Turn supplied document evidence into conservative atomic claim candidates."""

from __future__ import annotations

import json
import re
import uuid
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soul_ai.providers.base import LLMProvider, LLMRequest
from soul_ai.schemas import AtomicClaim
from soul_ai.extraction import PdfEvidence


class ClaimExtractionStatus(str, Enum):
    answered = "answered"
    insufficient_evidence = "insufficient_evidence"
    ambiguous = "ambiguous"


class ClaimExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClaimExtractionStatus
    answer: str | None = None
    claims: list[AtomicClaim] = Field(default_factory=list)
    reason: str | None = None


class _ModelClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_text: str = Field(min_length=1)
    entity: str | None = None
    attribute: str | None = None
    value: str | None = None
    unit: str | None = None
    temporal_context: str | None = None
    source_evidence_ids: list[str] = Field(min_length=1)


class _ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClaimExtractionStatus
    answer: str | None = None
    claims: list[_ModelClaim] = Field(default_factory=list)
    reason: str | None = None


class ClaimExtractionError(ValueError):
    """The provider output could not be safely interpreted as claim candidates."""


_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "were",
    "will", "with", "under", "between", "effective", "dated", "date", "amount",
    "states", "shows", "lists", "provides", "according", "document", "evidence",
}
_TOKEN_RE = re.compile(r"[\w]+(?:[.,][\w]+)*", re.UNICODE)
_DATE_RE = re.compile(
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{4}\b|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,)?\s+\d{4}\b|"
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b|"
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<!\w)\d[\d,]*(?:\.\d+)?%?(?!\w)")


class ClaimExtractor:
    """Generate fact candidates from provided PDF evidence using an injected LLM.

    The provider is asked to return JSON only. The code checks source IDs and
    performs a conservative lexical grounding check before returning any claim.
    These checks prevent clearly unsupported additions but are not verification;
    Phase 3 must independently assess every candidate against its cited source.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def extract(
        self,
        question: str,
        evidence: Sequence[PdfEvidence],
    ) -> ClaimExtractionResult:
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")
        if not evidence:
            return ClaimExtractionResult(
                status=ClaimExtractionStatus.insufficient_evidence,
                reason="No retrieved document evidence was supplied.",
            )

        evidence_by_id = {item.evidence_id: item for item in evidence}
        if len(evidence_by_id) != len(evidence):
            raise ValueError("evidence IDs must be unique within a request")
        evidence_payload = [
            {
                "evidence_id": item.evidence_id,
                "filename": item.filename,
                "page_number": item.page_number,
                "extracted_text": item.extracted_text,
            }
            for item in evidence
        ]
        request = LLMRequest(
            temperature=0.0,
            system_prompt=(
                "Extract only facts explicitly supported by the supplied evidence. "
                "Do not use outside knowledge or infer missing facts. Split compound "
                "sentences into one independently verifiable fact per claim. Keep each "
                "date, amount, unit, currency, condition, and entity in its own claim "
                "where independently verifiable. Cite only supplied evidence IDs. "
                "If the evidence cannot answer the question, return status "
                "insufficient_evidence with no answer and no claims. If source text "
                "supports materially different interpretations, return status ambiguous "
                "with no answer and no claims. Return a JSON object only with keys "
                "status, answer, claims, reason. Each claim must contain claim_text, "
                "entity, attribute, value, unit, temporal_context, source_evidence_ids."
            ),
            user_prompt=json.dumps(
                {"question": question, "evidence": evidence_payload},
                ensure_ascii=False,
            ),
        )
        response = await self.provider.generate(request)
        try:
            output = _ModelOutput.model_validate_json(response.text)
        except (ValidationError, ValueError) as exc:
            raise ClaimExtractionError("LLM returned invalid claim extraction JSON") from exc

        if output.status != ClaimExtractionStatus.answered:
            if output.claims or output.answer:
                raise ClaimExtractionError(
                    "Non-answered extraction must not include an answer or claims"
                )
            return ClaimExtractionResult(
                status=output.status,
                reason=(
                    "The supplied evidence is ambiguous for this question."
                    if output.status == ClaimExtractionStatus.ambiguous
                    else "The supplied evidence is insufficient to answer the question."
                ),
            )
        if not output.answer or not output.claims:
            return ClaimExtractionResult(
                status=ClaimExtractionStatus.insufficient_evidence,
                reason="No supported atomic claims could be constructed.",
            )

        claims: list[AtomicClaim] = []
        cited_text: list[str] = []
        for candidate in output.claims:
            unknown_ids = set(candidate.source_evidence_ids) - evidence_by_id.keys()
            if unknown_ids:
                return self._unsupported("A candidate referenced evidence that was not supplied.")
            source_text = " ".join(
                evidence_by_id[item_id].extracted_text
                for item_id in candidate.source_evidence_ids
            )
            if not self._lexically_grounded(candidate, source_text) or self._is_compound(
                candidate.claim_text
            ):
                return self._unsupported(
                    "A candidate was unsupported or combined multiple numeric/date facts."
                )
            cited_text.append(source_text)
            claims.append(
                AtomicClaim(
                    claim_id=str(uuid.uuid4()),
                    claim_text=candidate.claim_text.strip(),
                    entity=candidate.entity,
                    attribute=candidate.attribute,
                    value=candidate.value,
                    unit=candidate.unit,
                    temporal_context=candidate.temporal_context,
                    source_evidence_ids=candidate.source_evidence_ids,
                )
            )

        if not self._text_grounded(output.answer, " ".join(cited_text)):
            return self._unsupported("The answer included details not found in cited evidence.")
        return ClaimExtractionResult(
            status=ClaimExtractionStatus.answered,
            answer=output.answer.strip(),
            claims=claims,
        )

    @staticmethod
    def _unsupported(reason: str) -> ClaimExtractionResult:
        return ClaimExtractionResult(
            status=ClaimExtractionStatus.insufficient_evidence,
            reason=reason,
        )

    @classmethod
    def _lexically_grounded(cls, claim: _ModelClaim, source_text: str) -> bool:
        # ``attribute`` is a schema label (for example, "expiration date") and
        # may normalize wording; proposition text and concrete values stay grounded.
        values = [claim.claim_text, claim.entity, claim.value, claim.unit,
                  claim.temporal_context]
        return all(cls._text_grounded(value, source_text) for value in values if value)

    @classmethod
    def _text_grounded(cls, text: str, source_text: str) -> bool:
        source_tokens = {token.casefold() for token in _TOKEN_RE.findall(source_text)}
        content_tokens = {
            token.casefold()
            for token in _TOKEN_RE.findall(text)
            if token.casefold() not in _STOP_WORDS
        }
        return bool(content_tokens) and content_tokens.issubset(source_tokens)

    @staticmethod
    def _is_compound(claim_text: str) -> bool:
        """Conservatively reject claims that bundle multiple dates or numbers."""
        dates = list(_DATE_RE.finditer(claim_text))
        remainder = _DATE_RE.sub(" ", claim_text)
        numbers = _NUMBER_RE.findall(remainder)
        years = [value for value in numbers if re.fullmatch(r"(?:19|20)\d{2}", value)]
        amounts_or_counts = [value for value in numbers if value not in years]
        return len(dates) > 1 or len(years) > 1 or len(amounts_or_counts) > 1
