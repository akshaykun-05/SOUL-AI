from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from soul_ai.schemas import (
    AtomicClaim,
    Contradiction,
    DocumentMetadata,
    ExtractedEvidence,
    VerificationResult,
)


def test_document_metadata_accepts_valid_data():
    document = DocumentMetadata(
        document_id="doc-1",
        filename="contract.pdf",
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=1024,
        page_count=3,
        uploaded_at=datetime.now(timezone.utc),
    )

    assert document.filename == "contract.pdf"
    assert document.page_count == 3


def test_evidence_requires_a_positive_page_number():
    with pytest.raises(ValidationError):
        ExtractedEvidence(
            evidence_id="ev-1",
            document_id="doc-1",
            filename="contract.pdf",
            page_number=0,
            supporting_text="Effective on 1 January 2025.",
        )


def test_verification_result_accepts_supported_evidence():
    evidence = ExtractedEvidence(
        evidence_id="ev-1",
        document_id="doc-1",
        filename="contract.pdf",
        page_number=2,
        supporting_text="The term begins on 1 January 2025.",
    )
    claim = AtomicClaim(claim_id="claim-1", text="The term begins on 1 January 2025.")
    result = VerificationResult(
        claim_id=claim.claim_id,
        status="supported",
        confidence=0.99,
        evidence=[evidence],
        rationale="The evidence explicitly states the start date.",
    )

    assert result.status == "supported"
    assert result.evidence[0].page_number == 2


def test_contradiction_requires_two_claims_and_evidence_items():
    with pytest.raises(ValidationError):
        Contradiction(
            contradiction_id="con-1",
            claim_ids=["claim-1"],
            evidence_ids=["ev-1", "ev-2"],
            description="Conflicting dates.",
            severity="high",
        )
