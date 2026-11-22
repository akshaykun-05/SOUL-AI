"""Backend-facing integration contracts and orchestration."""

from .contracts import (
    BenchmarkRequest, ClaimsRequest, ContradictionRequest, EngineError, EngineResult,
    EvidenceQuery, PdfUpload, VerificationRequest,
)
from .factory import create_service_from_environment
from .service import SOULAIEngineService

__all__ = [
    "BenchmarkRequest", "ClaimsRequest", "ContradictionRequest", "EngineError",
    "EngineResult", "EvidenceQuery", "PdfUpload", "SOULAIEngineService",
    "VerificationRequest", "create_service_from_environment",
]
