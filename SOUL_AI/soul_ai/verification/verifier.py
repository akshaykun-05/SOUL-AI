"""Independent claim verification against original extracted evidence."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soul_ai.extraction import PdfEvidence
from soul_ai.providers.base import LLMProvider, LLMRequest
from soul_ai.schemas import AtomicClaim


class ClaimVerificationStatus(str, Enum):
    supported = "SUPPORTED"
    contradicted = "CONTRADICTED"
    unsupported = "UNSUPPORTED"


class EvidenceQuote(BaseModel):
    """A verbatim snippet tied to its original filename and page."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class ClaimVerificationResult(BaseModel):
    """Verification outcome; citations contain only checked source snippets."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    status: ClaimVerificationStatus
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    requires_human_review: bool


class _VerifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ClaimVerificationStatus
    evidence: list[EvidenceQuote] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    requires_human_review: bool = False


class VerificationError(ValueError):
    """Raised for invalid inputs or provider transport failures."""


class ClaimVerifier:
    """Run a dedicated verifier pass over all original evidence for each claim.

    Inject a verifier-specific provider instance, separate from the claim
    extraction call site. Candidate ``source_evidence_ids`` are deliberately
    ignored: every original evidence item is supplied to the verifier so it can
    find contradictory or more relevant source text independently.
    """

    def __init__(self, verifier_provider: LLMProvider) -> None:
        self.verifier_provider = verifier_provider

    async def verify(
        self,
        claim: AtomicClaim,
        original_evidence: Sequence[PdfEvidence],
    ) -> ClaimVerificationResult:
        if not original_evidence:
            return self._unsupported(claim, "No original extracted evidence was supplied.")

        request = LLMRequest(
            temperature=0.0,
            system_prompt=(
                "You are an independent claim verifier. Do not trust an answer or its "
                "candidate citations. Inspect every supplied ORIGINAL extracted evidence "
                "item yourself. Mark SUPPORTED only when a source explicitly states the "
                "claim or directly entails it with unambiguous evidence. Mark CONTRADICTED "
                "only when a source explicitly states an incompatible fact. Otherwise mark "
                "UNSUPPORTED. Semantic similarity, topical relevance, implication, or "
                "absence of a fact is not proof. Preserve units, currency, dates, entities, "
                "and conditions; normalize numeric formats only when the values are "
                "deterministically equivalent. If ambiguous, use UNSUPPORTED and set "
                "requires_human_review true. Return only exact, contiguous quotations from "
                "the supplied evidence, with the exact supplied filename and 1-based page. "
                "Do not invent citations. For SUPPORTED/CONTRADICTED, cite the exact "
                "supporting/conflicting snippet. For UNSUPPORTED, return no evidence. "
                "Return JSON only with keys status, evidence, reason, requires_human_review."
            ),
            user_prompt=json.dumps(
                {
                    "claim": {"claim_id": claim.claim_id, "claim_text": claim.claim_text},
                    "original_evidence": [
                        {
                            "filename": item.filename,
                            "page_number": item.page_number,
                            "text": item.extracted_text,
                            "extraction_method": item.extraction_method.value,
                            "confidence": item.confidence,
                        }
                        for item in original_evidence
                    ],
                },
                ensure_ascii=False,
            ),
        )
        try:
            response = await self.verifier_provider.generate(request)
        except Exception as exc:
            return self._unsupported(
                claim,
                f"Independent verifier provider failed ({type(exc).__name__}); human review is required.",
                requires_human_review=True,
            )

        try:
            verdict = _VerifierOutput.model_validate_json(response.text)
        except (ValidationError, ValueError):
            return self._unsupported(
                claim,
                "Verifier output was invalid; independent human review is required.",
                requires_human_review=True,
            )

        if verdict.status == ClaimVerificationStatus.unsupported:
            return self._unsupported(
                claim,
                verdict.reason,
                requires_human_review=verdict.requires_human_review,
            )

        checked_quotes = self._validate_quotes(verdict.evidence, original_evidence)
        if checked_quotes is None or not checked_quotes:
            return self._unsupported(
                claim,
                "Verifier citation did not exactly match the original evidence; human review is required.",
                requires_human_review=True,
            )

        if (
            verdict.status == ClaimVerificationStatus.supported
            and not self._numeric_details_match(claim.claim_text, checked_quotes)
        ):
            return self._unsupported(
                claim,
                "The numeric details in the claim do not match its cited text; human review is required.",
                requires_human_review=True,
            )

        # OCR with low or missing confidence is not sufficient to support a
        # definitive machine verdict, even if the quote is textually present.
        cited_ocr = self._cited_low_confidence_ocr(checked_quotes, original_evidence)
        if cited_ocr:
            return ClaimVerificationResult(
                claim_id=claim.claim_id,
                claim_text=claim.claim_text,
                status=ClaimVerificationStatus.unsupported,
                evidence=[],
                reason="Cited OCR text has low or unknown confidence; human review is required.",
                requires_human_review=True,
            )

        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            claim_text=claim.claim_text,
            status=verdict.status,
            evidence=checked_quotes,
            reason=verdict.reason,
            requires_human_review=verdict.requires_human_review,
        )

    async def verify_many(
        self,
        claims: Sequence[AtomicClaim],
        original_evidence: Sequence[PdfEvidence],
    ) -> list[ClaimVerificationResult]:
        """Verify each distinct claim independently and preserve input order."""
        ids = [claim.claim_id for claim in claims]
        if len(ids) != len(set(ids)):
            raise ValueError("claim IDs must be unique within a verification request")
        results = []
        for claim in claims:
            results.append(await self.verify(claim, original_evidence))
        return results

    @staticmethod
    def _validate_quotes(
        quotes: Sequence[EvidenceQuote], evidence: Sequence[PdfEvidence]
    ) -> list[EvidenceQuote] | None:
        checked: list[EvidenceQuote] = []
        for quote in quotes:
            matched = any(
                item.filename == quote.filename
                and item.page_number == quote.page_number
                and quote.text in item.extracted_text
                for item in evidence
            )
            if not matched:
                return None
            if quote not in checked:
                checked.append(quote)
        return checked

    @staticmethod
    def _cited_low_confidence_ocr(
        quotes: Sequence[EvidenceQuote], evidence: Sequence[PdfEvidence]
    ) -> bool:
        for quote in quotes:
            for item in evidence:
                if (
                    item.filename == quote.filename
                    and item.page_number == quote.page_number
                    and quote.text in item.extracted_text
                    and item.extraction_method.value == "ocr"
                    and (item.confidence is None or item.confidence < 0.75)
                ):
                    return True
        return False

    @staticmethod
    def _numeric_details_match(claim_text: str, quotes: Sequence[EvidenceQuote]) -> bool:
        """Require deterministic numeric agreement before accepting support.

        Recognizes plain numbers and common multipliers, including lakh/lac and
        crore. It guards against an incorrect SUPPORTED label when a quote is
        topically relevant but states a different amount, count, or year.
        """
        pattern = re.compile(
            r"(?<![\w])(?P<number>\d+(?:,\d{2,3})*(?:\.\d+)?)"
            r"\s*(?P<scale>lakhs?|lacs?|crores?|millions?|billions?|thousands?)?",
            re.IGNORECASE,
        )
        multipliers = {
            "lakh": Decimal(100_000),
            "lac": Decimal(100_000),
            "crore": Decimal(10_000_000),
            "million": Decimal(1_000_000),
            "billion": Decimal(1_000_000_000),
            "thousand": Decimal(1_000),
        }

        def values(text: str) -> list[Decimal]:
            result: list[Decimal] = []
            for match in pattern.finditer(text):
                try:
                    number = Decimal(match.group("number").replace(",", ""))
                except InvalidOperation:
                    continue
                scale = (match.group("scale") or "").lower().rstrip("s")
                if scale == "lacs":
                    scale = "lac"
                result.append((number * multipliers.get(scale, Decimal(1))).normalize())
            return result

        quote_text = " ".join(quote.text for quote in quotes)
        currencies = {
            "INR": r"(?:\bINR\b|\bRs\.?\b|\brupees?\b|₹)",
            "USD": r"(?:\bUSD\b|\bUS\$|\$|\bdollars?\b)",
            "EUR": r"(?:\bEUR\b|€|\beuros?\b)",
            "GBP": r"(?:\bGBP\b|£|\bpounds?\b)",
            "JPY": r"(?:\bJPY\b|¥|\byen\b)",
        }
        claim_currency = {
            code for code, regex in currencies.items()
            if re.search(regex, claim_text, re.IGNORECASE)
        }
        quote_currency = {
            code for code, regex in currencies.items()
            if re.search(regex, quote_text, re.IGNORECASE)
        }
        if claim_currency and not claim_currency.issubset(quote_currency):
            return False

        expected = values(claim_text)
        if not expected:
            return True
        available = values(quote_text)
        for expected_value in expected:
            try:
                available.remove(expected_value)
            except ValueError:
                return False
        return True

    @staticmethod
    def _unsupported(
        claim: AtomicClaim,
        reason: str,
        *,
        requires_human_review: bool = False,
    ) -> ClaimVerificationResult:
        return ClaimVerificationResult(
            claim_id=claim.claim_id,
            claim_text=claim.claim_text,
            status=ClaimVerificationStatus.unsupported,
            evidence=[],
            reason=reason,
            requires_human_review=requires_human_review,
        )
