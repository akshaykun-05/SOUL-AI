"""Retrieve evidence, extract claims, independently verify, then answer or abstain."""

from __future__ import annotations

import re
from collections import defaultdict
from enum import Enum
from typing import Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from soul_ai.claim_extraction import ClaimExtractionStatus, ClaimExtractor
from soul_ai.contradictions import ContradictionDetector, ContradictionFinding
from soul_ai.extraction import PdfEvidence
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import (
    ClaimVerificationResult,
    ClaimVerificationStatus,
    ClaimVerifier,
    EvidenceQuote,
)


INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Insufficient evidence. The uploaded documents do not contain enough information "
    "to answer this question."
)


class EvidenceIndex(Protocol):
    """Retrieval interface implemented by an indexed-document store."""

    def retrieve(self, question: str, *, limit: int = 20) -> list[PdfEvidence]:
        """Return relevant original evidence in retrieval order."""
        ...


class InMemoryEvidenceIndex:
    """Small deterministic lexical index for local use and tests.

    Production applications can implement ``EvidenceIndex`` over a vector or
    hybrid index. This baseline does not claim semantic retrieval; terms must
    overlap with source text.
    """

    _STOP_WORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "does",
        "for", "from", "how", "in", "is", "it", "of", "on", "or", "the", "to",
        "was", "what", "when", "where", "which", "who", "with", "would",
    }

    def __init__(self, evidence: Sequence[PdfEvidence] = ()) -> None:
        self._evidence: list[PdfEvidence] = []
        self._postings: dict[str, dict[int, int]] = defaultdict(dict)
        for item in evidence:
            self.add(item)

    def add(self, evidence: PdfEvidence) -> None:
        if any(item.evidence_id == evidence.evidence_id for item in self._evidence):
            raise ValueError(f"duplicate evidence_id: {evidence.evidence_id}")
        position = len(self._evidence)
        self._evidence.append(evidence)
        frequencies: dict[str, int] = defaultdict(int)
        for term in self._terms(evidence.extracted_text):
            frequencies[term] += 1
        for term, frequency in frequencies.items():
            self._postings[term][position] = frequency

    def retrieve(self, question: str, *, limit: int = 20) -> list[PdfEvidence]:
        if limit < 1:
            raise ValueError("limit must be positive")
        terms = self._terms(question)
        if not terms:
            return []
        scores: dict[int, int] = defaultdict(int)
        for term in terms:
            for position, frequency in self._postings.get(term, {}).items():
                scores[position] += min(frequency, 3)
        scored = [
            (score, -position, self._evidence[position])
            for position, score in scores.items()
        ]
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in scored[:limit]]

    @classmethod
    def _terms(cls, text: str) -> list[str]:
        return [
            term for term in re.findall(r"[\w]+", text.casefold())
            if len(term) > 1 and term not in cls._STOP_WORDS
        ]


class ResponseStatus(str, Enum):
    answered = "answered"
    insufficient_evidence = "insufficient_evidence"
    conflict = "conflict"


class VerifiedAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    evidence: list[EvidenceQuote] = Field(min_length=1)


class ZeroGuessingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ResponseStatus
    answer: str
    claims: list[VerifiedAnswerClaim] = Field(default_factory=list)
    conflicts: list[ContradictionFinding] = Field(default_factory=list)
    requires_human_review: bool = False
    # Internal evaluation trace; intentionally excluded from user/API JSON.
    evaluation_trace: list["VerificationTraceEntry"] = Field(default_factory=list, exclude=True)


class VerificationTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["answer", "document"]
    document_ids: list[str] = Field(default_factory=list)
    claim: AtomicClaim
    verification: ClaimVerificationResult


class ZeroGuessingEngine:
    """Coordinate retrieval, answer candidate generation, verification, and abstention."""

    def __init__(
        self,
        *,
        retriever: EvidenceIndex,
        claim_extractor: ClaimExtractor,
        verifier: ClaimVerifier,
        contradiction_detector: ContradictionDetector | None = None,
        retrieval_limit: int = 20,
    ) -> None:
        if retrieval_limit < 1:
            raise ValueError("retrieval_limit must be positive")
        self.retriever = retriever
        self.claim_extractor = claim_extractor
        self.verifier = verifier
        self.contradiction_detector = contradiction_detector or ContradictionDetector()
        self.retrieval_limit = retrieval_limit

    async def answer(
        self,
        question: str,
        *,
        document_versions: Mapping[str, str] | None = None,
    ) -> ZeroGuessingResponse:
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")

        retrieved = self.retriever.retrieve(question, limit=self.retrieval_limit)
        if not retrieved:
            return self._abstain()
        self._validate_retrieval(retrieved)

        try:
            candidate = await self.claim_extractor.extract(question, retrieved)
        except Exception:
            candidate = None
        evaluation_trace: list[VerificationTraceEntry] = []
        candidate_verifications: list[ClaimVerificationResult] = []
        if candidate is not None and candidate.claims:
            try:
                candidate_verifications = await self.verifier.verify_many(candidate.claims, retrieved)
            except Exception:
                candidate_verifications = []
            candidate_by_id = {claim.claim_id: claim for claim in candidate.claims}
            evaluation_trace.extend(
                VerificationTraceEntry(
                    scope="answer",
                    document_ids=list(dict.fromkeys(source.document_id for source in retrieved)),
                    claim=candidate_by_id[item.claim_id],
                    verification=item,
                )
                for item in candidate_verifications
                if item.claim_id in candidate_by_id
            )

        # Extract and verify each document's answer facts separately. This finds
        # opposing supported values even if answer generation picked one side.
        local_claims: list[AtomicClaim] = []
        local_verifications: list[ClaimVerificationResult] = []
        local_scan_uncertain = False
        by_document: dict[str, list[PdfEvidence]] = defaultdict(list)
        for item in retrieved:
            by_document[item.document_id].append(item)
        for document_evidence in by_document.values():
            try:
                local = await self.claim_extractor.extract(question, document_evidence)
            except Exception:
                local_scan_uncertain = True
                continue
            if local.status != ClaimExtractionStatus.answered:
                if local.status == ClaimExtractionStatus.ambiguous:
                    local_scan_uncertain = True
                continue
            if not local.claims:
                local_scan_uncertain = True
                continue
            local_claims.extend(local.claims)
            try:
                reports = await self.verifier.verify_many(local.claims, document_evidence)
            except Exception:
                local_scan_uncertain = True
                continue
            local_verifications.extend(reports)
            local_by_id = {claim.claim_id: claim for claim in local.claims}
            evaluation_trace.extend(
                VerificationTraceEntry(
                    scope="document",
                    document_ids=list(dict.fromkeys(source.document_id for source in document_evidence)),
                    claim=local_by_id[item.claim_id],
                    verification=item,
                )
                for item in reports
                if item.claim_id in local_by_id
            )
            if (
                len(reports) != len(local.claims)
                or len({report.claim_id for report in reports}) != len(reports)
                or any(
                    report.status != ClaimVerificationStatus.supported
                    or report.requires_human_review
                    for report in reports
                )
            ):
                local_scan_uncertain = True

        conflicts = self.contradiction_detector.detect(
            local_claims,
            local_verifications,
            retrieved,
            document_versions=document_versions,
        )
        if conflicts:
            if local_scan_uncertain:
                conflicts = [
                    item.model_copy(update={"requires_human_review": True})
                    for item in conflicts
                ]
            return ZeroGuessingResponse(
                status=ResponseStatus.conflict,
                answer=self._conflict_answer(conflicts),
                conflicts=conflicts,
                requires_human_review=any(item.requires_human_review for item in conflicts),
                evaluation_trace=evaluation_trace,
            )

        if (
            candidate is None
            or candidate.status != ClaimExtractionStatus.answered
            or not candidate.claims
            or local_scan_uncertain
        ):
            return self._abstain(evaluation_trace)

        reports = {item.claim_id: item for item in candidate_verifications}
        if len(reports) != len(candidate.claims):
            return self._abstain(evaluation_trace)
        verified_claims: list[VerifiedAnswerClaim] = []
        for claim in candidate.claims:
            report = reports.get(claim.claim_id)
            if (
                report is None
                or report.status != ClaimVerificationStatus.supported
                or report.requires_human_review
                or report.claim_text != claim.claim_text
                or not report.evidence
                or not self._citations_match(report.evidence, retrieved)
            ):
                return self._abstain(evaluation_trace)
            verified_claims.append(
                VerifiedAnswerClaim(
                    claim_id=claim.claim_id,
                    claim_text=claim.claim_text,
                    evidence=report.evidence,
                )
            )

        answer = "\n".join(
            f"{claim.claim_text} " + " ".join(
                f"[{quote.filename}, p. {quote.page_number}]" for quote in claim.evidence
            )
            for claim in verified_claims
        )
        return ZeroGuessingResponse(
            status=ResponseStatus.answered,
            answer=answer,
            claims=verified_claims,
            evaluation_trace=evaluation_trace,
        )

    @staticmethod
    def _validate_retrieval(evidence: Sequence[PdfEvidence]) -> None:
        ids = [item.evidence_id for item in evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("retriever returned duplicate evidence IDs")

    @staticmethod
    def _citations_match(
        citations: Sequence[EvidenceQuote], evidence: Sequence[PdfEvidence]
    ) -> bool:
        return all(
            any(
                source.filename == citation.filename
                and source.page_number == citation.page_number
                and citation.text in source.extracted_text
                for source in evidence
            )
            for citation in citations
        )

    @staticmethod
    def _abstain(
        trace: Sequence[VerificationTraceEntry] = (),
    ) -> ZeroGuessingResponse:
        return ZeroGuessingResponse(
            status=ResponseStatus.insufficient_evidence,
            answer=INSUFFICIENT_EVIDENCE_MESSAGE,
            evaluation_trace=list(trace),
        )

    @staticmethod
    def _conflict_answer(conflicts: Sequence[ContradictionFinding]) -> str:
        lines = ["The uploaded documents contain conflicting information:"]
        for item in conflicts:
            lines.append(
                f"- {item.entity} — {item.attribute}: {item.value_1} "
                f"({item.source_1.filename}, p. {item.source_1.page_number}) versus "
                f"{item.value_2} ({item.source_2.filename}, p. {item.source_2.page_number})."
            )
        lines.append("Please review the cited documents before relying on either value.")
        return "\n".join(lines)
