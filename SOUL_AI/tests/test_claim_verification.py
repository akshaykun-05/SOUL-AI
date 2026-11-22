from __future__ import annotations

import asyncio
import json

from soul_ai.extraction import ExtractionMethod, PdfEvidence
from soul_ai.providers.base import LLMResponse
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import ClaimVerificationStatus, ClaimVerifier


class FakeVerifierProvider:
    def __init__(self, *payloads: dict):
        self.payloads = list(payloads)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        return LLMResponse(text=json.dumps(payload), provider="verifier-test", model="test")


def source(
    evidence_id: str,
    text: str,
    *,
    filename: str = "loan_agreement.pdf",
    page: int = 3,
    method: ExtractionMethod = ExtractionMethod.text,
    confidence: float | None = None,
) -> PdfEvidence:
    return PdfEvidence(
        evidence_id=evidence_id,
        document_id="doc-1",
        filename=filename,
        page_number=page,
        extracted_text=text,
        start_offset=0,
        end_offset=len(text),
        extraction_method=method,
        confidence=confidence,
    )


def claim(text: str, evidence_ids: list[str] | None = None) -> AtomicClaim:
    return AtomicClaim(
        claim_id="C001",
        claim_text=text,
        source_evidence_ids=evidence_ids or [],
    )


def verdict(status: str, evidence: list[dict] | None = None, *, review=False,
            reason="Checked against the supplied original text.") -> dict:
    return {
        "status": status,
        "evidence": evidence or [],
        "reason": reason,
        "requires_human_review": review,
    }


def run(verifier: ClaimVerifier, candidate: AtomicClaim, originals: list[PdfEvidence]):
    return asyncio.run(verifier.verify(candidate, originals))


def test_supported_claim_returns_exact_verbatim_citation():
    text = "The sanctioned loan amount is Rs. 5,00,000."
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3,
        "text": "The sanctioned loan amount is Rs. 5,00,000.",
    }]))

    result = run(ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"), [source("ev-1", text)])

    assert result.status == ClaimVerificationStatus.supported
    assert result.evidence[0].text == text
    assert result.evidence[0].filename == "loan_agreement.pdf"
    assert result.evidence[0].page_number == 3
    assert result.requires_human_review is False


def test_numeric_equivalence_accepts_lakh_and_indian_digit_grouping():
    text = "The sanctioned loan amount is Rs. 5,00,000."
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3, "text": text,
    }]))

    result = run(ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"), [source("ev-1", text)])

    assert result.status == ClaimVerificationStatus.supported


def test_wrong_numeric_support_label_is_rejected_deterministically():
    text = "The sanctioned loan amount is Rs. 2,50,000."
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3, "text": text,
    }]))

    result = run(ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"), [source("ev-1", text)])

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.requires_human_review is True
    assert result.evidence == []


def test_currency_mismatch_cannot_be_marked_supported():
    text = "The sanctioned loan amount is USD 500,000."
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3, "text": text,
    }]))

    result = run(ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"), [source("ev-1", text)])

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.requires_human_review is True


def test_contradicted_claim_returns_exact_conflicting_snippet():
    text = "The sanctioned loan amount is Rs. 2,50,000."
    provider = FakeVerifierProvider(verdict("CONTRADICTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3, "text": text,
    }]))

    result = run(ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"), [source("ev-1", text)])

    assert result.status == ClaimVerificationStatus.contradicted
    assert result.evidence[0].text == text


def test_irrelevant_topical_evidence_is_not_treated_as_support():
    text = "The borrower requested a loan amount of Rs. 5,00,000."
    provider = FakeVerifierProvider(verdict(
        "UNSUPPORTED", reason="A request is not evidence of the sanctioned amount."
    ))

    result = run(
        ClaimVerifier(provider),
        claim("The sanctioned loan amount is Rs. 5 lakh", ["ev-1"]),
        [source("ev-1", text)],
    )

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.evidence == []
    prompt = provider.requests[0].user_prompt
    assert "original_evidence" in prompt
    assert "The borrower requested" in prompt


def test_verifier_reviews_originals_instead_of_candidate_citations():
    irrelevant = source("ev-candidate", "The borrower requested Rs. 5,00,000.", page=2)
    conflicting = source("ev-other", "The sanctioned amount is Rs. 2,50,000.", page=3)
    provider = FakeVerifierProvider(verdict("CONTRADICTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3,
        "text": "The sanctioned amount is Rs. 2,50,000.",
    }]))

    result = run(
        ClaimVerifier(provider),
        claim("The sanctioned amount is Rs. 5,00,000.", ["ev-candidate"]),
        [irrelevant, conflicting],
    )

    assert result.status == ClaimVerificationStatus.contradicted
    assert result.evidence[0].page_number == 3
    assert "The sanctioned amount is Rs. 2,50,000." in provider.requests[0].user_prompt
    assert "ev-candidate" not in provider.requests[0].user_prompt


def test_fabricated_or_nonmatching_quote_is_downgraded_to_unsupported():
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3,
        "text": "The sanctioned amount is Rs. 5,00,000.",
    }]))

    result = run(
        ClaimVerifier(provider),
        claim("The sanctioned amount is Rs. 5,00,000."),
        [source("ev-1", "The sanctioned amount is Rs. 2,50,000.")],
    )

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.evidence == []
    assert result.requires_human_review is True


def test_ambiguous_verdict_becomes_unsupported_and_requires_review():
    provider = FakeVerifierProvider(verdict("UNSUPPORTED", review=True,
                                             reason="Two conflicting dates appear."))

    result = run(ClaimVerifier(provider), claim("The agreement starts in March."),
                 [source("ev-1", "Starts March 1; addendum says starts April 1.")])

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.requires_human_review is True
    assert result.evidence == []


def test_low_confidence_ocr_cannot_produce_definitive_verification():
    text = "The sanctioned loan amount is Rs. 5,00,000."
    provider = FakeVerifierProvider(verdict("SUPPORTED", [{
        "filename": "loan_agreement.pdf", "page_number": 3, "text": text,
    }]))

    result = run(
        ClaimVerifier(provider), claim("The loan amount is Rs. 5 lakh"),
        [source("ev-ocr", text, method=ExtractionMethod.ocr, confidence=0.4)],
    )

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.evidence == []
    assert result.requires_human_review is True


def test_missing_original_evidence_is_unsupported_without_provider_call():
    provider = FakeVerifierProvider()

    result = run(ClaimVerifier(provider), claim("Some claim"), [])

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.evidence == []
    assert provider.requests == []


def test_provider_failure_still_returns_unsupported_result_for_claim():
    class FailedProvider:
        async def generate(self, request):
            raise TimeoutError("provider timeout")

    result = run(
        ClaimVerifier(FailedProvider()),
        claim("The loan amount is Rs. 5 lakh"),
        [source("ev-1", "The sanctioned loan amount is Rs. 5,00,000.")],
    )

    assert result.status == ClaimVerificationStatus.unsupported
    assert result.requires_human_review is True
    assert result.evidence == []


def test_verify_many_returns_one_independent_result_per_claim():
    first_text = "The term is 36 months."
    second_text = "The interest rate is 7 percent."
    provider = FakeVerifierProvider(
        verdict("SUPPORTED", [{"filename": "loan_agreement.pdf", "page_number": 3,
                               "text": first_text}]),
        verdict("SUPPORTED", [{"filename": "loan_agreement.pdf", "page_number": 3,
                               "text": second_text}]),
    )
    verifier = ClaimVerifier(provider)
    claims = [claim(first_text), AtomicClaim(claim_id="C002", claim_text=second_text)]
    originals = [source("ev-1", first_text + " " + second_text)]

    results = asyncio.run(verifier.verify_many(claims, originals))

    assert len(results) == 2
    assert [result.claim_id for result in results] == ["C001", "C002"]
    assert len(provider.requests) == 2
