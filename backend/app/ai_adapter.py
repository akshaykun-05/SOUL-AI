from dataclasses import dataclass
from importlib import import_module
from uuid import uuid5

from pydantic import ValidationError

from .ai_schemas import AIProcessingResult, AIStatus, EvidenceReference, VerifiedClaim
from .config import ENV_FILE
from .document_schemas import DocumentResponse

PROMPT = "What concrete facts does this document explicitly state?"
UNVERIFIED_WARNING = "Some candidates could not be verified or require human review."
SCOPE_LIMITS = ["cross_document_contradictions"]
CAPABILITIES = [
    "page_text", "claim_extraction", "claim_verification", "page_citations",
    "cross_document_contradictions",
]


class EngineUnavailable(Exception):
    pass


@dataclass
class Engine:
    evidence_type: type
    extractor: object
    verifier: object
    request_error: type[Exception]
    model_identity: tuple[str, str, str | None]
    verification_provider: "VerificationProvider | None" = None
    detector: object | None = None
    claim_type: type | None = None
    verification_type: type | None = None


class VerificationProvider:
    """Keep provider failures distinguishable from an unsupported claim."""

    def __init__(self, provider):
        self.provider = provider
        self.failure: Exception | None = None

    async def generate(self, request):
        try:
            return await self.provider.generate(request)
        except Exception as exc:
            self.failure = exc
            raise


def inspect_engine() -> tuple[AIStatus, Engine | None]:
    try:
        import_module("soul_ai")
    except ModuleNotFoundError as exc:
        if exc.name == "soul_ai":
            return unavailable("Install the SOUL AI engine package in the backend environment.")
        return unavailable("An engine dependency could not be imported.", package_ready=True)
    except (ImportError, OSError):
        return unavailable("The engine package could not be loaded.")

    try:
        from soul_ai.claim_extraction import ClaimExtractor
        from soul_ai.config import LLMSettings
        from soul_ai.contradictions import ContradictionDetector
        from soul_ai.extraction import PdfEvidence
        from soul_ai.providers import create_configured_provider
        from soul_ai.providers.openai_compatible import (
            ProviderConfigurationError,
            ProviderRequestError,
        )
        from soul_ai.schemas import AtomicClaim
        from soul_ai.verification import ClaimVerificationResult, ClaimVerifier
        interfaces = (
            PdfEvidence, ClaimExtractor.extract, ClaimVerifier.verify_many,
            ContradictionDetector.detect, create_configured_provider,
        )
        if not all(callable(interface) for interface in interfaces):
            raise ImportError
    except (ImportError, OSError, AttributeError):
        return unavailable("An engine dependency or required interface is unavailable.", package_ready=True)

    try:
        settings = LLMSettings(_env_file=ENV_FILE, _env_file_encoding="utf-8")
    except (ValidationError, OSError, UnicodeError):
        return unavailable(
            "SOUL_LLM settings could not be read. Check the environment and backend/.env.",
            package_ready=True, dependencies_ready=True,
        )

    key_present = bool(settings.api_key and settings.api_key.get_secret_value().strip())
    if not key_present:
        return unavailable(
            "SOUL_LLM_API_KEY is missing or blank. Set your own key in backend/.env or the process environment.",
            package_ready=True, dependencies_ready=True, api_key_present=False,
        )

    try:
        extractor = ClaimExtractor(create_configured_provider(settings))
        verification_provider = VerificationProvider(create_configured_provider(settings))
        verifier = ClaimVerifier(verification_provider)
    except ProviderConfigurationError:
        return unavailable(
            "Provider configuration was rejected. Use SOUL_LLM_PROVIDER=openai or openai-compatible.",
            package_ready=True, dependencies_ready=True, api_key_present=True,
        )
    except (ImportError, OSError):
        return unavailable(
            "A model dependency could not be loaded.", package_ready=True, api_key_present=True,
        )

    return AIStatus(
        status="available",
        capabilities=CAPABILITIES,
        package_ready=True,
        dependencies_ready=True,
        provider_configured=True,
        api_key_present=True,
        diagnostics=["Provider configured locally. Remote reachability and credentials are checked during processing."],
    ), Engine(
        PdfEvidence, extractor, verifier, ProviderRequestError,
        (settings.provider, settings.model, settings.base_url),
        verification_provider,
        ContradictionDetector(),
        AtomicClaim,
        ClaimVerificationResult,
    )


def unavailable(
    message: str,
    *,
    package_ready: bool = False,
    dependencies_ready: bool = False,
    api_key_present: bool | None = None,
) -> tuple[AIStatus, None]:
    return AIStatus(
        status="unavailable",
        capabilities=CAPABILITIES if dependencies_ready else [],
        package_ready=package_ready,
        dependencies_ready=dependencies_ready,
        provider_configured=False,
        api_key_present=api_key_present,
        diagnostics=[message],
    ), None


def page_evidence(document: DocumentResponse, engine: Engine) -> list:
    return [
        engine.evidence_type(
            evidence_id=str(uuid5(document.document_id, f"page:{page.page_number}")),
            document_id=str(document.document_id),
            filename=document.filename,
            page_number=page.page_number,
            extracted_text=page.text,
            start_offset=0,
            end_offset=len(page.text),
            extraction_method="text",
        )
        for page in document.pages if page.text.strip()
    ]


async def extract_and_verify(evidence: list, engine: Engine) -> tuple[dict, list]:
    evidence_ids = {item.evidence_id for item in evidence}

    try:
        extraction = await engine.extractor.extract(PROMPT, evidence)
        if extraction.status not in {"answered", "ambiguous", "insufficient_evidence"}:
            raise ValueError("Unexpected extraction status")
        candidates = {claim.claim_id: claim for claim in extraction.claims}
        if len(candidates) != len(extraction.claims) or (
            extraction.status != "answered" and candidates
        ):
            raise ValueError("Invalid claim collection")
        for claim in candidates.values():
            if not claim.source_evidence_ids or not set(claim.source_evidence_ids) <= evidence_ids:
                raise ValueError("Invalid candidate source reference")
        reports = await engine.verifier.verify_many(extraction.claims, evidence) if candidates else []
        if engine.verification_provider and engine.verification_provider.failure:
            raise engine.verification_provider.failure
    except engine.request_error as exc:
        raise EngineUnavailable("The configured model provider could not complete the request.") from exc
    except ImportError as exc:
        raise EngineUnavailable("A model dependency could not be loaded.") from exc

    if len(reports) != len(candidates) or {report.claim_id for report in reports} != candidates.keys():
        raise ValueError("Verification results do not match candidate IDs")

    return candidates, reports


async def process_document(document: DocumentResponse, engine: Engine) -> AIProcessingResult:
    evidence = page_evidence(document, engine)
    candidates, reports = await extract_and_verify(evidence, engine)
    pages = {item.page_number: item.extracted_text for item in evidence}
    claims = []
    references = {}
    warnings = []
    if len(evidence) < document.page_count:
        warnings.append("Pages without extracted text were skipped.")
    for report in reports:
        candidate = candidates[report.claim_id]
        if report.claim_text != candidate.claim_text or report.status not in {
            "SUPPORTED", "CONTRADICTED", "UNSUPPORTED"
        }:
            raise ValueError("Invalid verification result")
        if report.status != "SUPPORTED" or report.requires_human_review:
            continue
        citations = []
        for quote in report.evidence:
            if quote.filename != document.filename or not quote.text.strip() or (
                quote.text not in pages.get(quote.page_number, "")
            ):
                raise ValueError("A verification citation does not match stored text")
            evidence_id = str(uuid5(document.document_id, f"page:{quote.page_number}"))
            citation = EvidenceReference(
                evidence_id=evidence_id, document_id=document.document_id,
                filename=quote.filename, page_number=quote.page_number, text=quote.text,
            )
            citations.append(citation)
            references[(evidence_id, quote.text)] = citation
        claims.append(VerifiedClaim(
            claim_id=report.claim_id, claim_text=report.claim_text,
            evidence_references=citations,
        ))

    if not claims:
        warnings.append("The engine returned no independently supported claims.")
    if len(claims) < len(candidates):
        warnings.append(UNVERIFIED_WARNING)
    return AIProcessingResult(
        document_id=document.document_id,
        processing_status="completed" if claims else "insufficient_evidence",
        engine_status="available", page_count=document.page_count,
        claims=claims, evidence_references=list(references.values()),
        unavailable_capabilities=SCOPE_LIMITS, warnings=warnings,
    )
