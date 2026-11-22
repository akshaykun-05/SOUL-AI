from __future__ import annotations

from soul_ai.contradictions import ContradictionDetector, ContradictionType
from soul_ai.extraction import ExtractionMethod, PdfEvidence
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import (
    ClaimVerificationResult,
    ClaimVerificationStatus,
    EvidenceQuote,
)


def fact(claim_id: str, value: str, *, unit=None, entity="Acme Ltd", attribute="loan amount",
         context=None, evidence_id=None) -> AtomicClaim:
    return AtomicClaim(
        claim_id=claim_id,
        claim_text=f"{entity} {attribute} is {value}.",
        entity=entity,
        attribute=attribute,
        value=value,
        unit=unit,
        temporal_context=context,
        source_evidence_ids=[evidence_id or f"ev-{claim_id}"],
    )


def original(evidence_id: str, document_id: str, filename: str, page: int, snippet: str) -> PdfEvidence:
    return PdfEvidence(
        evidence_id=evidence_id,
        document_id=document_id,
        filename=filename,
        page_number=page,
        extracted_text=snippet,
        start_offset=0,
        end_offset=len(snippet),
        extraction_method=ExtractionMethod.text,
    )


def supported(claim: AtomicClaim, filename: str, page: int, snippet: str) -> ClaimVerificationResult:
    return ClaimVerificationResult(
        claim_id=claim.claim_id,
        claim_text=claim.claim_text,
        status=ClaimVerificationStatus.supported,
        evidence=[EvidenceQuote(filename=filename, page_number=page, text=snippet)],
        reason="Explicit source statement.",
        requires_human_review=False,
    )


def detect_pair(
    first: AtomicClaim,
    second: AtomicClaim,
    text1: str,
    text2: str,
    *,
    version1: str | None = None,
    version2: str | None = None,
):
    file1, file2 = "first.pdf", "second.pdf"
    docs = [
        original("ev-1", "doc-1", file1, 1, text1),
        original("ev-2", "doc-2", file2, 2, text2),
    ]
    versions = {}
    if version1:
        versions["doc-1"] = version1
    if version2:
        versions["doc-2"] = version2
    return ContradictionDetector().detect(
        [first, second],
        [supported(first, file1, 1, text1), supported(second, file2, 2, text2)],
        docs,
        document_versions=versions,
    )


def test_equivalent_annual_and_monthly_amounts_are_not_contradictions():
    first = fact("c1", "Rs. 6,00,000", unit="annually")
    second = fact("c2", "Rs. 50,000", unit="monthly", attribute="sanctioned amount")

    findings = detect_pair(first, second, "Annual amount: Rs. 6,00,000.", "Monthly amount: Rs. 50,000.")

    assert findings == []


def test_equivalent_lakh_and_grouped_inr_amounts_are_not_contradictions():
    first = fact("c1", "5 lakh", unit="INR")
    second = fact("c2", "Rs. 500,000")

    findings = detect_pair(first, second, "Loan amount is 5 lakh INR.", "Loan amount is Rs. 500,000.")

    assert findings == []


def test_different_loan_amounts_return_both_source_snippets():
    first = fact("c1", "Rs. 6,00,000")
    second = fact("c2", "Rs. 5,00,000")

    findings = detect_pair(first, second, "The loan amount is Rs. 6,00,000.",
                           "The loan amount is Rs. 5,00,000.")

    assert len(findings) == 1
    item = findings[0]
    assert item.contradiction_type == ContradictionType.amount_conflict
    assert item.source_1.snippet == "The loan amount is Rs. 6,00,000."
    assert item.source_2.snippet == "The loan amount is Rs. 5,00,000."
    assert item.source_1.filename == "first.pdf"
    assert item.source_2.page_number == 2
    assert item.requires_human_review is False


def test_different_currency_codes_conflict_even_when_numeric_amount_matches():
    first = fact("c1", "INR 500,000")
    second = fact("c2", "USD 500,000")

    findings = detect_pair(first, second, "Loan amount: INR 500,000.", "Loan amount: USD 500,000.")

    assert len(findings) == 1
    assert findings[0].contradiction_type == ContradictionType.amount_conflict
    assert findings[0].requires_human_review is True
    assert "cannot be compared reliably" in findings[0].explanation


def test_equivalent_named_date_formats_are_not_contradictions():
    first = fact("c1", "30 June 2027", entity="Agreement", attribute="deadline")
    second = fact("c2", "June 30, 2027", entity="Agreement", attribute="due date")

    findings = detect_pair(first, second, "Deadline is 30 June 2027.", "Deadline is June 30, 2027.")

    assert findings == []


def test_different_deadline_dates_are_reported():
    first = fact("c1", "30 June 2027", entity="Agreement", attribute="deadline")
    second = fact("c2", "31 July 2027", entity="Agreement", attribute="due date")

    findings = detect_pair(first, second, "Deadline is 30 June 2027.", "Deadline is 31 July 2027.")

    assert len(findings) == 1
    assert findings[0].contradiction_type == ContradictionType.date_conflict
    assert findings[0].source_1.snippet.endswith("30 June 2027.")


def test_revised_document_value_is_reported_as_possible_version_change():
    first = fact("c1", "Rs. 6,00,000")
    second = fact("c2", "Rs. 5,00,000")

    findings = detect_pair(
        first,
        second,
        "Loan amount is Rs. 6,00,000.",
        "Revised loan amount is Rs. 5,00,000.",
        version1="v1 / 2024-01-01",
        version2="v2 / 2024-02-01",
    )

    assert len(findings) == 1
    assert findings[0].contradiction_type == ContradictionType.version_change
    assert findings[0].source_1.document_version == "v1 / 2024-01-01"
    assert findings[0].source_2.document_version == "v2 / 2024-02-01"
    assert findings[0].requires_human_review is True


def test_different_values_at_distinct_times_are_marked_temporal_change():
    first = fact("c1", "5%", attribute="interest rate", context="2023")
    second = fact("c2", "7%", attribute="interest rate", context="2025")

    findings = detect_pair(first, second, "Interest rate was 5% in 2023.",
                           "Interest rate is 7% in 2025.")

    assert len(findings) == 1
    assert findings[0].contradiction_type == ContradictionType.temporal_change
    assert findings[0].requires_human_review is True


def test_interest_rate_conflict_and_identity_conflict_are_detected():
    first = fact("c1", "5%", entity="Acme", attribute="interest rate")
    second = fact("c2", "7 percent", entity="Acme", attribute="rate of interest")
    rates = detect_pair(first, second, "Interest rate is 5%.", "Interest rate is 7 percent.")
    assert rates[0].contradiction_type == ContradictionType.interest_rate_conflict

    borrower1 = fact("c3", "Alex Green", entity="Borrower", attribute="name")
    borrower2 = fact("c4", "Jamie Green", entity="Borrower", attribute="name")
    identity = detect_pair(borrower1, borrower2, "Borrower: Alex Green.", "Borrower: Jamie Green.")
    assert identity[0].contradiction_type == ContradictionType.identity_conflict


def test_different_contractual_conditions_require_human_review():
    first = fact("c1", "written notice is required", entity="Either party", attribute="condition")
    second = fact("c2", "written notice is not required", entity="Either party", attribute="condition")

    findings = detect_pair(first, second, "Termination: written notice is required.",
                           "Termination: written notice is not required.")

    assert len(findings) == 1
    assert findings[0].contradiction_type == ContradictionType.condition_conflict
    assert findings[0].requires_human_review is True


def test_percent_and_unmarked_number_are_not_assumed_equivalent():
    first = fact("c1", "5%", entity="Acme", attribute="interest rate")
    second = fact("c2", "5", entity="Acme", attribute="interest rate")

    findings = detect_pair(first, second, "Interest rate is 5%.", "Interest rate is 5.")

    assert len(findings) == 1
    assert findings[0].requires_human_review is True


def test_unverified_claims_unrelated_entities_and_attributes_are_not_compared():
    first = fact("c1", "Rs. 6,00,000")
    second = fact("c2", "Rs. 5,00,000", entity="Other Ltd")
    docs = [
        original("ev-1", "doc-1", "first.pdf", 1, "Loan amount is Rs. 6,00,000."),
        original("ev-2", "doc-2", "second.pdf", 2, "Loan amount is Rs. 5,00,000."),
    ]
    unverified = ClaimVerificationResult(
        claim_id=second.claim_id,
        claim_text=second.claim_text,
        status=ClaimVerificationStatus.unsupported,
        evidence=[],
        reason="Not found.",
        requires_human_review=False,
    )

    assert ContradictionDetector().detect(
        [first, second], [supported(first, "first.pdf", 1, docs[0].extracted_text), unverified], docs
    ) == []

    unrelated_entity = fact("c2", "Rs. 5,00,000", entity="Other Ltd")
    assert detect_pair(first, unrelated_entity, "Loan amount is Rs. 6,00,000.",
                       "Loan amount is Rs. 5,00,000.") == []

    unrelated_attribute = fact("c2", "Rs. 5,00,000", attribute="annual fee")
    assert detect_pair(first, unrelated_attribute, "Loan amount is Rs. 6,00,000.",
                       "Annual fee is Rs. 5,00,000.") == []


def test_citation_not_found_in_original_extraction_is_discarded():
    first = fact("c1", "Rs. 6,00,000")
    second = fact("c2", "Rs. 5,00,000")
    docs = [
        original("ev-1", "doc-1", "first.pdf", 1, "Loan amount is Rs. 6,00,000."),
        original("ev-2", "doc-2", "second.pdf", 2, "Loan amount is Rs. 5,00,000."),
    ]
    invalid_report = supported(second, "second.pdf", 2, "fabricated amount is Rs. 5,00,000")

    findings = ContradictionDetector().detect(
        [first, second],
        [supported(first, "first.pdf", 1, docs[0].extracted_text), invalid_report],
        docs,
    )

    assert findings == []


def test_review_required_verification_is_not_used_as_confirmed_fact():
    first = fact("c1", "Rs. 6,00,000")
    second = fact("c2", "Rs. 5,00,000")
    docs = [
        original("ev-1", "doc-1", "first.pdf", 1, "Loan amount is Rs. 6,00,000."),
        original("ev-2", "doc-2", "second.pdf", 2, "Loan amount is Rs. 5,00,000."),
    ]
    reviewed = supported(second, "second.pdf", 2, docs[1].extracted_text).model_copy(
        update={"requires_human_review": True}
    )

    findings = ContradictionDetector().detect(
        [first, second],
        [supported(first, "first.pdf", 1, docs[0].extracted_text), reviewed],
        docs,
    )

    assert findings == []
