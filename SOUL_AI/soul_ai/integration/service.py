"""Backend-facing orchestration over the independently testable engine phases."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from soul_ai.claim_extraction import ClaimExtractor
from soul_ai.contradictions import ContradictionDetector
from soul_ai.extraction import PdfEvidence, PdfIngestor
from soul_ai.integration.contracts import EngineError, EngineResult, PdfUpload, ResultStatus
from soul_ai.response_engine import InMemoryEvidenceIndex, ZeroGuessingEngine
from soul_ai.schemas import AtomicClaim
from soul_ai.verification import ClaimVerificationResult, ClaimVerifier


class SOULAIEngineService:
    """Synchronous extraction/retrieval plus async LLM-backed pipeline calls.

    An instance owns an in-memory evidence index. Production backends may persist
    uploads and implement a durable EvidenceIndex, while reusing these methods.
    """

    def __init__(self, *, claim_provider=None, verifier_provider=None,
                 ingestor: PdfIngestor | None = None, retrieval_limit: int = 20):
        self.claim_provider = claim_provider
        self.verifier_provider = verifier_provider
        self.ingestor = ingestor or PdfIngestor()
        self.retrieval_limit = retrieval_limit
        self._documents: dict[str, dict] = {}
        self._evidence: list[PdfEvidence] = []

    @staticmethod
    def _safe_request_id(request_id: str) -> str:
        return request_id if isinstance(request_id, str) and request_id.strip() else "invalid-request"

    @staticmethod
    def _ok(request_id: str, data) -> EngineResult:
        return EngineResult(request_id=SOULAIEngineService._safe_request_id(request_id),
                            status=ResultStatus.success, data=data)

    @staticmethod
    def _err(request_id: str, code: str, message: str, retryable: bool = False) -> EngineResult:
        return EngineResult(request_id=SOULAIEngineService._safe_request_id(request_id), status=ResultStatus.error,
                            error=EngineError(code=code, message=message, retryable=retryable))

    @staticmethod
    def _failure(request_id: str, exc: Exception) -> EngineResult:
        from soul_ai.extraction import PdfIngestionError
        if isinstance(exc, PdfIngestionError):
            return SOULAIEngineService._err(request_id, "DOCUMENT_EXTRACTION_FAILED", str(exc))
        if isinstance(exc, (ValueError, TypeError)):
            return SOULAIEngineService._err(request_id, "INVALID_REQUEST", str(exc))
        return SOULAIEngineService._err(request_id, "ENGINE_FAILURE",
                                        "The AI engine could not complete the request.", True)

    def extract_documents(self, request_id: str, uploads: Sequence[PdfUpload]) -> EngineResult:
        try:
            if not request_id.strip() or not uploads:
                raise ValueError("request_id and at least one PDF upload are required")
            ids = [item.document_id for item in uploads]
            if len(ids) != len(set(ids)) or any(item in self._documents for item in ids):
                raise ValueError("document IDs must be unique and not already indexed")
            results = []
            for upload in uploads:
                result = self.ingestor.ingest(upload.bytes(), document_id=upload.document_id,
                                              filename=upload.filename)
                results.append(result)
            # Commit only when every requested PDF passed parser validation.
            for upload, result in zip(uploads, results):
                self._documents[upload.document_id] = {
                    "document_id": result.document_id, "filename": result.filename,
                    "sha256": result.sha256, "page_count": result.page_count,
                    "status": result.status.value, "warnings": result.warnings,
                    "size_bytes": len(upload.bytes()),
                }
                self._evidence.extend(result.evidence)
            return self._ok(request_id, {"documents": [x.model_dump(mode="json") for x in results],
                                         "evidence_count": sum(len(x.evidence) for x in results)})
        except Exception as exc:
            return self._failure(request_id, exc)

    def retrieve_evidence(self, request_id: str, question: str,
                          document_ids: Sequence[str] | None = None, limit: int | None = None) -> EngineResult:
        try:
            if not question.strip():
                raise ValueError("question must not be empty")
            allowed = set(document_ids) if document_ids else None
            available = [item for item in self._evidence if allowed is None or item.document_id in allowed]
            found = InMemoryEvidenceIndex(available).retrieve(question, limit=limit or self.retrieval_limit)
            return self._ok(request_id, {"evidence": [item.model_dump(mode="json") for item in found]})
        except Exception as exc:
            return self._failure(request_id, exc)

    def _select_evidence(self, evidence_ids: Sequence[str] | None = None) -> list[PdfEvidence]:
        if evidence_ids is None:
            return list(self._evidence)
        requested = set(evidence_ids)
        selected = [item for item in self._evidence if item.evidence_id in requested]
        if len(selected) != len(requested):
            raise ValueError("one or more evidence IDs are not indexed")
        return selected

    async def extract_claims(self, request_id: str, question: str,
                            evidence_ids: Sequence[str] | None = None) -> EngineResult:
        try:
            if self.claim_provider is None:
                return self._err(request_id, "PROVIDER_NOT_CONFIGURED", "Claim extraction provider is not configured.")
            evidence = self._select_evidence(evidence_ids)
            from soul_ai.claim_extraction import ClaimExtractor
            result = await ClaimExtractor(self.claim_provider).extract(question, evidence)
            return self._ok(request_id, result.model_dump(mode="json"))
        except Exception as exc:
            return self._failure(request_id, exc)

    async def verify_claims(self, request_id: str, claims: Sequence[AtomicClaim],
                            evidence_ids: Sequence[str] | None = None) -> EngineResult:
        try:
            if self.verifier_provider is None:
                return self._err(request_id, "PROVIDER_NOT_CONFIGURED", "Independent verifier provider is not configured.")
            reports = await ClaimVerifier(self.verifier_provider).verify_many(claims, self._select_evidence(evidence_ids))
            return self._ok(request_id, {"verifications": [x.model_dump(mode="json") for x in reports]})
        except Exception as exc:
            return self._failure(request_id, exc)

    def detect_contradictions(self, request_id: str, claims: Sequence[AtomicClaim],
                              verifications: Sequence[ClaimVerificationResult],
                              evidence_ids: Sequence[str] | None = None,
                              document_versions: Mapping[str, str] | None = None) -> EngineResult:
        try:
            findings = ContradictionDetector().detect(claims, verifications,
                self._select_evidence(evidence_ids), document_versions=document_versions)
            return self._ok(request_id, {"contradictions": [x.model_dump(mode="json") for x in findings]})
        except Exception as exc:
            return self._failure(request_id, exc)

    async def answer(self, request_id: str, question: str,
                     document_ids: Sequence[str] | None = None,
                     document_versions: Mapping[str, str] | None = None) -> EngineResult:
        try:
            if self.claim_provider is None or self.verifier_provider is None:
                return self._err(request_id, "PROVIDER_NOT_CONFIGURED",
                                 "Both claim extraction and independent verifier providers are required.")
            if not question.strip():
                raise ValueError("question must not be empty")
            evidence = self._select_evidence()
            if document_ids is not None:
                selected_ids = set(document_ids)
                evidence = [item for item in evidence if item.document_id in selected_ids]
            engine = ZeroGuessingEngine(
                retriever=InMemoryEvidenceIndex(evidence),
                claim_extractor=ClaimExtractor(self.claim_provider),
                verifier=ClaimVerifier(self.verifier_provider),
                retrieval_limit=self.retrieval_limit,
            )
            result = await engine.answer(question, document_versions=document_versions)
            return self._ok(request_id, result.model_dump(mode="json"))
        except Exception as exc:
            return self._failure(request_id, exc)

    async def evaluate_benchmark(self, request_id: str, *, input_path: str,
                                 labels_path: str, output_path: str) -> EngineResult:
        """Run the Phase 6 harness using this service's complete response pipeline."""
        try:
            import json
            from soul_ai.evaluation.benchmark import evaluate_run, file_sha256, validate_dataset
            inputs = json.loads(Path(input_path).read_text(encoding="utf-8"))
            labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
            validate_dataset(inputs, labels)
            labels_by_id = {x["case_id"]: x for x in labels["cases"]}
            outputs = []
            for case in inputs["cases"]:
                if self.claim_provider is None or self.verifier_provider is None:
                    outputs.append((case, None, None, "PROVIDER_NOT_CONFIGURED"))
                    continue
                # Benchmark inputs carry their own evidence; gold labels never
                # enter this execution path.
                evidence = [PdfEvidence.model_validate(item) for item in case["evidence"]]
                engine = ZeroGuessingEngine(
                    retriever=InMemoryEvidenceIndex(evidence),
                    claim_extractor=ClaimExtractor(self.claim_provider),
                    verifier=ClaimVerifier(self.verifier_provider),
                    retrieval_limit=self.retrieval_limit,
                )
                response = await engine.answer(
                    case["question"],
                    document_versions=case.get("document_versions") or None,
                )
                outputs.append((case, response.model_dump(mode="json"), response, None))
            report = evaluate_run(input_cases=inputs["cases"], labels_by_id=labels_by_id,
                input_sha256=file_sha256(input_path), labels_sha256=file_sha256(labels_path),
                factory_name="SOULAIEngineService", outputs=outputs)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
            return self._ok(request_id, report)
        except Exception as exc:
            return self._failure(request_id, exc)
