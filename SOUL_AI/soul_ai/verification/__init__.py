"""Independent evidence-based verification of atomic claims."""

from .verifier import (
    ClaimVerificationResult,
    ClaimVerificationStatus,
    ClaimVerifier,
    EvidenceQuote,
    VerificationError,
)

__all__ = [
    "ClaimVerificationResult",
    "ClaimVerificationStatus",
    "ClaimVerifier",
    "EvidenceQuote",
    "VerificationError",
]
