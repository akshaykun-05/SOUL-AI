from __future__ import annotations

import asyncio

from soul_ai.claim_extraction import ClaimExtractionResult, ClaimExtractionStatus
from soul_ai.extraction import ExtractionMethod, PdfEvidence
from soul_ai.response_engine import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    InMemoryEvidenceIndex,
    ResponseStatus,
    ZeroGuessingEngine,
)
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import (
    ClaimVerificationResult,
    ClaimVerificationStatus,
    EvidenceQuote,
)


def evidence(evidence_id: str, document_id: str, filename: str, page: int, text: str) -> PdfEvidence:
    return PdfEvidence(
        evidence_id=evidence_id,
        document_id=document_id,
        filename=filename,
        page_number=page,
        extracted_text=text,
        start_offset=0,
        end_offset=len(text),
        extraction_method=ExtractionMethod.text,
    )


def claim(claim_id: str, text: str, *, value: str | None = None, evidence_ids=None) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        claim_text=text,
        entity="Acme Ltd",
        attribute="loan amount" if value else "fact",
        value=value,
        source_evidence_ids=evidence_ids or [],
    )


def extraction_result(status: ClaimExtractionStatus, claims=None, answer=None):
    return ClaimExtractionResult(
        status=status,
        answer=answer,
        claims=claims or [],
        reason=None if status == ClaimExtractionStatus.answered else "No supported answer.",
    )


def report(candidate: AtomicClaim, status: ClaimVerificationStatus, quotes=(), *, review=False):
    return ClaimVerificationResult(
        claim_id=candidate.claim_id,
        claim_text=candidate.claim_text,
        status=status,
        evidence=list(quotes),
        reason="Independent verifier result.",
        requires_human_review=review,
    )


class StubExtractor:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def extract(self, question, evidence_items):
        self.calls.append((question, [item.evidence_id for item in evidence_items]))
        return self.results.pop(0)


class StubVerifier:
    def __init__(self, *batches):
        self.batches = list(batches)
        self.calls = []

    async def verify_many(self, claims, original_evidence):
        self.calls.append(([item.claim_id for item in claims], [e.evidence_id for e in original_evidence]))
        return self.batches.pop(0)


def run(engine: ZeroGuessingEngine, question: str, **kwargs):
    return asyncio.run(engine.answer(question, **kwargs))


def test_supported_answer_has_checked_citation_for_every_claim():
    source = evidence("ev-1", "doc-1", "loan.pdf", 3, "Acme Ltd loan amount is Rs. 5,00,000.")
    candidate = claim("c1", "Acme Ltd loan amount is Rs. 5,00,000.",
                      value="Rs. 5,00,000", evidence_ids=["ev-1"])
    quote = EvidenceQuote(filename="loan.pdf", page_number=3, text=source.extracted_text)
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [candidate], "generated answer"),
        extraction_result(ClaimExtractionStatus.answered, [candidate], "generated answer"),
    )
    verifier = StubVerifier(
        [report(candidate, ClaimVerificationStatus.supported, [quote])],
        [report(candidate, ClaimVerificationStatus.supported, [quote])],
    )
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is Acme loan amount?")

    assert result.status == ResponseStatus.answered
    assert result.claims[0].evidence == [quote]
    assert "[loan.pdf, p. 3]" in result.answer
    assert "generated answer" not in result.answer


def test_missing_answer_abstains_with_required_message():
    source = evidence("ev-1", "doc-1", "profile.pdf", 1, "Acme Ltd is registered in Pune.")
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.insufficient_evidence),
        extraction_result(ClaimExtractionStatus.insufficient_evidence),
    )
    verifier = StubVerifier()
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is Acme's annual revenue?")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.claims == []


def test_no_relevant_evidence_abstains_without_calling_models():
    extractor = StubExtractor()
    verifier = StubVerifier()
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex(), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is the contract amount?")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert extractor.calls == []
    assert verifier.calls == []


def test_conflicting_document_values_are_shown_with_both_citations():
    first = evidence("ev-1", "doc-1", "agreement.pdf", 2, "Acme Ltd loan amount is Rs. 6,00,000.")
    second = evidence("ev-2", "doc-2", "addendum.pdf", 4, "Acme Ltd loan amount is Rs. 5,00,000.")
    candidate = claim("candidate", "Acme Ltd loan amount is Rs. 6,00,000.",
                      value="Rs. 6,00,000", evidence_ids=["ev-1"])
    first_fact = claim("fact-1", "Acme Ltd loan amount is Rs. 6,00,000.",
                       value="Rs. 6,00,000", evidence_ids=["ev-1"])
    second_fact = claim("fact-2", "Acme Ltd loan amount is Rs. 5,00,000.",
                        value="Rs. 5,00,000", evidence_ids=["ev-2"])
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [candidate]),
        extraction_result(ClaimExtractionStatus.answered, [first_fact]),
        extraction_result(ClaimExtractionStatus.answered, [second_fact]),
    )
    verifier = StubVerifier(
        [report(candidate, ClaimVerificationStatus.contradicted)],
        [report(first_fact, ClaimVerificationStatus.supported, [EvidenceQuote(
            filename="agreement.pdf", page_number=2, text=first.extracted_text
        )])],
        [report(second_fact, ClaimVerificationStatus.supported, [EvidenceQuote(
            filename="addendum.pdf", page_number=4, text=second.extracted_text
        )])],
    )
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([first, second]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is Acme loan amount?")

    assert result.status == ResponseStatus.conflict
    assert len(result.conflicts) == 1
    assert "Rs. 6,00,000" in result.answer and "Rs. 5,00,000" in result.answer
    assert "agreement.pdf, p. 2" in result.answer
    assert "addendum.pdf, p. 4" in result.answer
    assert result.requires_human_review is False


def test_combined_evidence_claim_cites_every_supporting_page():
    first = evidence("ev-1", "doc-1", "terms.pdf", 1, "The agreement has a principal amount.")
    second = evidence("ev-2", "doc-1", "terms.pdf", 2, "The principal amount is Rs. 5,00,000.")
    combined = claim("c1", "The principal amount is Rs. 5,00,000 under the agreement.",
                     value="Rs. 5,00,000", evidence_ids=["ev-1", "ev-2"])
    quotes = [
        EvidenceQuote(filename="terms.pdf", page_number=1, text=first.extracted_text),
        EvidenceQuote(filename="terms.pdf", page_number=2, text=second.extracted_text),
    ]
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [combined]),
        extraction_result(ClaimExtractionStatus.answered, [combined]),
    )
    verifier = StubVerifier(
        [report(combined, ClaimVerificationStatus.supported, quotes)],
        [report(combined, ClaimVerificationStatus.supported, quotes)],
    )
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([first, second]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is the principal amount under the agreement?")

    assert result.status == ResponseStatus.answered
    assert {quote.page_number for quote in result.claims[0].evidence} == {1, 2}
    assert "p. 1" in result.answer and "p. 2" in result.answer
    assert verifier.calls[0][1] == ["ev-1", "ev-2"]


def test_misleading_assumption_is_rejected_when_verifier_does_not_support_it():
    source = evidence("ev-1", "doc-1", "accounts.pdf", 5, "Acme Ltd owes Rs. 2,00,000 to the bank.")
    unsupported_claim = claim("c1", "Acme Ltd has no debt.", value=None, evidence_ids=["ev-1"])
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [unsupported_claim]),
        extraction_result(ClaimExtractionStatus.insufficient_evidence),
    )
    verifier = StubVerifier([report(unsupported_claim, ClaimVerificationStatus.unsupported)])
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "Confirm that Acme Ltd has no debt.")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.claims == []


def test_important_unsupported_claim_abstains_entire_candidate_answer():
    source = evidence("ev-1", "doc-1", "terms.pdf", 1, "Acme Ltd principal is Rs. 5,00,000.")
    supported_fact = claim("c1", "Acme Ltd principal is Rs. 5,00,000.",
                           value="Rs. 5,00,000", evidence_ids=["ev-1"])
    unsupported_fact = claim("c2", "The interest rate is 7 percent.")
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [supported_fact, unsupported_fact]),
        extraction_result(ClaimExtractionStatus.answered, [supported_fact]),
    )
    verifier = StubVerifier(
        [
            report(supported_fact, ClaimVerificationStatus.supported, [EvidenceQuote(
                filename="terms.pdf", page_number=1, text=source.extracted_text
            )]),
            report(unsupported_fact, ClaimVerificationStatus.unsupported),
        ],
        [report(supported_fact, ClaimVerificationStatus.supported, [EvidenceQuote(
            filename="terms.pdf", page_number=1, text=source.extracted_text
        )])],
    )
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What are the principal and interest rate?")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.claims == []


def test_ambiguous_document_level_check_prevents_silent_answer():
    source = evidence("ev-1", "doc-1", "agreement.pdf", 1, "Acme Ltd loan amount is Rs. 5,00,000.")
    candidate = claim("c1", "Acme Ltd loan amount is Rs. 5,00,000.",
                      value="Rs. 5,00,000", evidence_ids=["ev-1"])
    quote = EvidenceQuote(filename="agreement.pdf", page_number=1, text=source.extracted_text)
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [candidate]),
        extraction_result(ClaimExtractionStatus.ambiguous),
    )
    verifier = StubVerifier([report(candidate, ClaimVerificationStatus.supported, [quote])])
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is Acme loan amount?")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE


def test_answer_abstains_if_verifier_citation_does_not_match_retrieved_source():
    source = evidence("ev-1", "doc-1", "loan.pdf", 3, "Acme Ltd loan amount is Rs. 5,00,000.")
    candidate = claim("c1", "Acme Ltd loan amount is Rs. 5,00,000.",
                      value="Rs. 5,00,000", evidence_ids=["ev-1"])
    fake_quote = EvidenceQuote(filename="loan.pdf", page_number=99, text="Invented citation.")
    extractor = StubExtractor(
        extraction_result(ClaimExtractionStatus.answered, [candidate]),
        extraction_result(ClaimExtractionStatus.answered, [candidate]),
    )
    verifier = StubVerifier(
        [report(candidate, ClaimVerificationStatus.supported, [fake_quote])],
        [report(candidate, ClaimVerificationStatus.supported, [fake_quote])],
    )
    engine = ZeroGuessingEngine(
        retriever=InMemoryEvidenceIndex([source]), claim_extractor=extractor, verifier=verifier
    )

    result = run(engine, "What is Acme loan amount?")

    assert result.status == ResponseStatus.insufficient_evidence
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE

