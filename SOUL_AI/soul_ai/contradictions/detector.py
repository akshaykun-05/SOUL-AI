"""Detect incompatible verified facts across original document sources."""

from __future__ import annotations

import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from soul_ai.extraction import PdfEvidence
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import ClaimVerificationResult, ClaimVerificationStatus


class ContradictionType(str, Enum):
    amount_conflict = "amount_conflict"
    date_conflict = "date_conflict"
    interest_rate_conflict = "interest_rate_conflict"
    identity_conflict = "identity_conflict"
    condition_conflict = "condition_conflict"
    value_conflict = "value_conflict"
    temporal_change = "temporal_change"
    version_change = "version_change"


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    snippet: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    document_version: str | None = None
    temporal_context: str | None = None


class ContradictionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contradiction_id: str = Field(min_length=1)
    entity: str = Field(min_length=1)
    attribute: str = Field(min_length=1)
    value_1: str = Field(min_length=1)
    value_2: str = Field(min_length=1)
    source_1: SourceFact
    source_2: SourceFact
    contradiction_type: ContradictionType
    explanation: str = Field(min_length=1)
    requires_human_review: bool


class _VerifiedFact:
    def __init__(
        self,
        claim: AtomicClaim,
        source: PdfEvidence,
        snippet: str,
        version: str | None,
    ) -> None:
        self.claim = claim
        self.source = source
        self.snippet = snippet
        self.version = version


_NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?P<number>\d+(?:,\d{2,3})*(?:\.\d+)?)"
    r"\s*(?P<scale>lakhs?|lacs?|crores?|millions?|billions?|thousands?)?",
    re.IGNORECASE,
)
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%Y", "%d %B %Y", "%d %b %Y",
    "%B %d %Y", "%B %d, %Y", "%b %d %Y", "%b %d, %Y",
)
_ATTRIBUTE_ALIASES = {
    "loan amount": "loan_amount",
    "sanctioned amount": "loan_amount",
    "sanctioned loan amount": "loan_amount",
    "principal amount": "loan_amount",
    "interest rate": "interest_rate",
    "rate of interest": "interest_rate",
    "annual interest rate": "interest_rate",
    "effective date": "effective_date",
    "commencement date": "start_date",
    "start date": "start_date",
    "expiry date": "deadline_date",
    "expiration date": "deadline_date",
    "due date": "deadline_date",
    "deadline": "deadline_date",
    "payment deadline": "deadline_date",
    "borrower name": "borrower_name",
    "lender name": "lender_name",
    "name": "name",
    "identity": "identity",
    "condition": "condition",
    "contractual condition": "condition",
}
_MONEY_ATTRIBUTES = {"loan_amount", "principal_amount", "amount", "fee", "rent", "salary"}
_DATE_ATTRIBUTES = {"effective_date", "start_date", "deadline_date", "date", "due_date"}
_CONDITION_ATTRIBUTES = {"condition", "contractual_condition", "termination_condition"}
_CURRENCY_PATTERNS = {
    "INR": r"(?:\bINR\b|\bRs\.?\b|\brupees?\b|₹)",
    "USD": r"(?:\bUSD\b|\bUS\$|\$|\bdollars?\b)",
    "EUR": r"(?:\bEUR\b|€|\beuros?\b)",
    "GBP": r"(?:\bGBP\b|£|\bpounds?\b)",
    "JPY": r"(?:\bJPY\b|¥|\byen\b)",
}
_SCALE_MULTIPLIERS = {
    "lakh": Decimal(100_000), "lac": Decimal(100_000),
    "crore": Decimal(10_000_000), "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000), "thousand": Decimal(1_000),
}
_PERIOD_FACTORS = {
    "annual": Decimal(1), "annually": Decimal(1), "yearly": Decimal(1), "per year": Decimal(1),
    "per annum": Decimal(1),
    "monthly": Decimal(12), "per month": Decimal(12), "month": Decimal(12),
    "weekly": Decimal(52), "per week": Decimal(52), "daily": Decimal(365), "per day": Decimal(365),
}


class ContradictionDetector:
    """Compare only independently supported claims and their verified quotes.

    ``document_versions`` maps document IDs to known version labels. Claims
    citing different known versions are reported as possible legitimate
    ``version_change`` findings and require human review.
    """

    def detect(
        self,
        claims: Sequence[AtomicClaim],
        verifications: Sequence[ClaimVerificationResult],
        original_evidence: Sequence[PdfEvidence],
        *,
        document_versions: Mapping[str, str] | None = None,
    ) -> list[ContradictionFinding]:
        claims_by_id = {item.claim_id: item for item in claims}
        if len(claims_by_id) != len(claims):
            raise ValueError("claim IDs must be unique")
        verifications_by_id = {item.claim_id: item for item in verifications}
        if len(verifications_by_id) != len(verifications):
            raise ValueError("verification reports must have unique claim IDs")
        evidence = list(original_evidence)
        versions = document_versions or {}

        facts: list[_VerifiedFact] = []
        for claim_id, report in verifications_by_id.items():
            claim = claims_by_id.get(claim_id)
            if (
                claim is None
                or claim.claim_text != report.claim_text
                or report.status != ClaimVerificationStatus.supported
                or report.requires_human_review
            ):
                continue
            for citation in report.evidence:
                matches = [
                    item for item in evidence
                    if item.filename == citation.filename
                    and item.page_number == citation.page_number
                    and citation.text in item.extracted_text
                ]
                if not matches or len({item.document_id for item in matches}) != 1:
                    continue
                match = matches[0]
                facts.append(
                    _VerifiedFact(
                        claim,
                        match,
                        citation.text,
                        versions.get(match.document_id),
                    )
                )

        findings: list[ContradictionFinding] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for index, first in enumerate(facts):
            for second in facts[index + 1 :]:
                if first.source.document_id == second.source.document_id:
                    continue
                if not self._compatible(first.claim, second.claim):
                    continue
                comparison = self._compare_values(first.claim, second.claim)
                if comparison is None or comparison[0] == "equal":
                    continue

                value_relation, conflict_type = comparison
                if first.version and second.version and first.version != second.version:
                    conflict_type = ContradictionType.version_change
                    explanation = (
                        f"The values differ between document versions {first.version!r} "
                        f"and {second.version!r}; this may be an intentional revision."
                    )
                    human_review = True
                elif (
                    first.claim.temporal_context
                    and second.claim.temporal_context
                    and self._normalize_context(first.claim.temporal_context)
                    != self._normalize_context(second.claim.temporal_context)
                    and conflict_type not in {ContradictionType.date_conflict}
                ):
                    conflict_type = ContradictionType.temporal_change
                    explanation = (
                        "The verified values differ across distinct temporal contexts; "
                        "this may describe a change over time."
                    )
                    human_review = True
                elif value_relation == "ambiguous":
                    explanation = (
                        "The values differ, but their units or date formats cannot be "
                        "compared reliably; human review is required."
                    )
                    human_review = True
                else:
                    explanation = (
                        f"Both documents support different values for the same entity and "
                        f"attribute ({first.claim.entity}, {first.claim.attribute})."
                    )
                    human_review = conflict_type == ContradictionType.condition_conflict

                key = (
                    self._normalize_entity(first.claim.entity or ""),
                    self._canonical_attribute(first.claim.attribute or ""),
                    min(first.claim.value or "", second.claim.value or ""),
                    max(first.claim.value or "", second.claim.value or ""),
                    conflict_type.value,
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    ContradictionFinding(
                        contradiction_id=str(uuid.uuid4()),
                        entity=first.claim.entity or "",
                        attribute=first.claim.attribute or "",
                        value_1=self._display_value(first.claim),
                        value_2=self._display_value(second.claim),
                        source_1=self._source(first),
                        source_2=self._source(second),
                        contradiction_type=conflict_type,
                        explanation=explanation,
                        requires_human_review=human_review,
                    )
                )
        return findings

    @classmethod
    def _compatible(cls, first: AtomicClaim, second: AtomicClaim) -> bool:
        if not first.entity or not second.entity or not first.attribute or not second.attribute:
            return False
        return (
            cls._normalize_entity(first.entity) == cls._normalize_entity(second.entity)
            and cls._canonical_attribute(first.attribute) == cls._canonical_attribute(second.attribute)
        )

    @staticmethod
    def _normalize_entity(value: str) -> str:
        return " ".join(re.findall(r"[\w]+", value.casefold()))

    @staticmethod
    def _canonical_attribute(value: str) -> str:
        normalized = " ".join(re.findall(r"[\w]+", value.casefold()))
        return _ATTRIBUTE_ALIASES.get(normalized, normalized.replace(" ", "_"))

    @classmethod
    def _compare_values(
        cls, first: AtomicClaim, second: AtomicClaim
    ) -> tuple[str, ContradictionType] | None:
        first_value = first.value
        second_value = second.value
        if not first_value or not second_value:
            return None
        attribute = cls._canonical_attribute(first.attribute or "")
        combined_first = f"{first_value} {first.unit or ''}"
        combined_second = f"{second_value} {second.unit or ''}"

        if attribute in _DATE_ATTRIBUTES or cls._looks_like_date(combined_first, combined_second):
            date1, ambiguous1 = cls._parse_date(first_value)
            date2, ambiguous2 = cls._parse_date(second_value)
            if date1 is None or date2 is None:
                if cls._normalize_context(first_value) == cls._normalize_context(second_value):
                    return "equal", ContradictionType.date_conflict
                return "ambiguous", ContradictionType.date_conflict
            return (
                ("equal", ContradictionType.date_conflict)
                if date1 == date2
                else ("different", ContradictionType.date_conflict)
            )

        num1 = cls._parse_number(combined_first)
        num2 = cls._parse_number(combined_second)
        if num1 is not None and num2 is not None:
            if num1[1] != num2[1]:
                return "ambiguous", cls._type_for_attribute(attribute)
            percent1 = bool(re.search(r"%|\bpercent\b", combined_first, re.I))
            percent2 = bool(re.search(r"%|\bpercent\b", combined_second, re.I))
            if percent1 != percent2:
                return "ambiguous", cls._type_for_attribute(attribute)
            period1 = cls._period_factor(combined_first)
            period2 = cls._period_factor(combined_second)
            if period1 is not None and period2 is not None:
                n1, n2 = num1[0] * period1, num2[0] * period2
            elif period1 == period2:
                n1, n2 = num1[0], num2[0]
            else:
                return "ambiguous", cls._type_for_attribute(attribute)
            if n1 == n2:
                return "equal", cls._type_for_attribute(attribute)
            return "different", cls._type_for_attribute(attribute)

        value1 = cls._normalize_context(combined_first)
        value2 = cls._normalize_context(combined_second)
        if value1 == value2:
            return "equal", cls._type_for_attribute(attribute)
        if attribute in _CONDITION_ATTRIBUTES:
            return "ambiguous", ContradictionType.condition_conflict
        if attribute in {"name", "identity", "borrower_name", "lender_name"}:
            return "different", ContradictionType.identity_conflict
        return "ambiguous", cls._type_for_attribute(attribute)

    @staticmethod
    def _type_for_attribute(attribute: str) -> ContradictionType:
        if attribute == "interest_rate":
            return ContradictionType.interest_rate_conflict
        if attribute in _MONEY_ATTRIBUTES:
            return ContradictionType.amount_conflict
        if attribute in _DATE_ATTRIBUTES:
            return ContradictionType.date_conflict
        if attribute in _CONDITION_ATTRIBUTES:
            return ContradictionType.condition_conflict
        if attribute in {"name", "identity", "borrower_name", "lender_name"}:
            return ContradictionType.identity_conflict
        return ContradictionType.value_conflict

    @staticmethod
    def _parse_number(value: str) -> tuple[Decimal, str | None] | None:
        match = _NUMBER_PATTERN.search(value)
        if not match:
            return None
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            return None
        scale = (match.group("scale") or "").lower().rstrip("s")
        if scale == "lacs":
            scale = "lac"
        number *= _SCALE_MULTIPLIERS.get(scale, Decimal(1))
        currency = next(
            (code for code, pattern in _CURRENCY_PATTERNS.items()
             if re.search(pattern, value, re.IGNORECASE)),
            None,
        )
        return number.normalize(), currency

    @staticmethod
    def _period_factor(value: str) -> Decimal | None:
        lowered = value.casefold()
        for phrase, factor in _PERIOD_FACTORS.items():
            if re.search(rf"\b{re.escape(phrase)}\b", lowered):
                return factor
        return None

    @staticmethod
    def _parse_date(value: str) -> tuple[date | None, bool]:
        cleaned = re.sub(r"\b(?:effective|expires?|due|on|by)\b", " ", value, flags=re.I)
        cleaned = re.sub(r"\bdate\b", " ", cleaned, flags=re.I)
        cleaned = " ".join(cleaned.replace(",", " ").split())
        for fmt in _DATE_FORMATS:
            try:
                from datetime import datetime

                return datetime.strptime(cleaned, fmt).date(), False
            except ValueError:
                pass
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", cleaned):
            return None, True
        return None, False

    @classmethod
    def _looks_like_date(cls, first: str, second: str) -> bool:
        return any(
            cls._parse_date(item)[0] is not None
            or bool(re.search(r"\b(?:19|20)\d{2}\b", item) and re.search(r"\b(?:date|expires?|deadline|due)\b", item, re.I))
            for item in (first, second)
        )

    @staticmethod
    def _normalize_context(value: str) -> str:
        return " ".join(re.findall(r"[\w]+", value.casefold()))

    @staticmethod
    def _display_value(claim: AtomicClaim) -> str:
        values = [claim.value, claim.unit]
        return " ".join(item.strip() for item in values if item and item.strip())

    @staticmethod
    def _source(fact: _VerifiedFact) -> SourceFact:
        return SourceFact(
            filename=fact.source.filename,
            page_number=fact.source.page_number,
            snippet=fact.snippet,
            document_id=fact.source.document_id,
            document_version=fact.version,
            temporal_context=fact.claim.temporal_context,
        )
