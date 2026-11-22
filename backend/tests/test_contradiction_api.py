import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app import ai_adapter, contradiction_service
from app.ai_schemas import AIStatus
from app.config import BACKEND_DIR, Settings
from app.document_models import ContradictionAnalysis, DocumentPage, VerifiedInputCache
from app.main import create_app
from test_documents import make_pdf, upload

ROUTE = "/api/ai/contradictions"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, upload_dir=tmp_path / "uploads", ai_cache_revision="test",
        database_url=f"sqlite:///{(tmp_path / 'comparison.sqlite3').as_posix()}",
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.syspath_prepend(str(BACKEND_DIR.parent / "SOUL_AI"))
    extraction = importlib.import_module("soul_ai.claim_extraction")
    verification = importlib.import_module("soul_ai.verification")
    claims = importlib.import_module("soul_ai.schemas")
    evidence = importlib.import_module("soul_ai.extraction")
    detector = importlib.import_module("soul_ai.contradictions")
    providers = importlib.import_module("soul_ai.providers.base")

    class ControlledProvider:
        async def generate(self, request):
            data = json.loads(request.user_prompt)
            if "question" in data:
                source = data["evidence"][-1]
                text = source["extracted_text"].strip()
                value = text.split()[-1].rstrip(".")
                result = {
                    "status": "answered", "answer": text, "reason": None,
                    "claims": [{
                        "claim_text": text, "entity": "Acme Ltd", "attribute": "loan amount",
                        "value": value, "unit": "INR", "temporal_context": None,
                        "source_evidence_ids": [source["evidence_id"]],
                    }],
                }
            else:
                source = data["original_evidence"][-1]
                result = {
                    "status": "SUPPORTED", "reason": "Exact supporting text.",
                    "requires_human_review": False, "evidence": [{
                        "filename": source["filename"], "page_number": source["page_number"],
                        "text": source["text"].strip(),
                    }],
                }
            return providers.LLMResponse(text=json.dumps(result), provider="test", model="test")

    class ProviderFailure(RuntimeError):
        pass

    extractor = extraction.ClaimExtractor(ControlledProvider())
    verifier = verification.ClaimVerifier(ControlledProvider())
    engine = ai_adapter.Engine(
        evidence.PdfEvidence,
        SimpleNamespace(extract=AsyncMock(wraps=extractor.extract)),
        SimpleNamespace(verify_many=AsyncMock(side_effect=verifier.verify_many)),
        ProviderFailure, ("controlled", "test", None),
        detector=SimpleNamespace(detect=Mock(side_effect=detector.ContradictionDetector().detect)),
        claim_type=claims.AtomicClaim, verification_type=verification.ClaimVerificationResult,
    )
    readiness = AIStatus(status="available", package_ready=True,
                         dependencies_ready=True, provider_configured=True)
    monkeypatch.setattr(ai_adapter, "inspect_engine", lambda: (readiness, engine))
    return engine


def put_document(client, index, amount=None, page=1):
    text = "" if amount is None else f"Acme Ltd loan amount is INR {amount}."
    response = upload(client, make_pdf(*([""] * (page - 1)), text), f"source-{index}.pdf")
    assert response.status_code == 201
    return response.json()["document_id"]


def compare(client, ids):
    return client.post(ROUTE, json={"document_ids": ids})


@pytest.mark.parametrize("count", [2, 3, 4, 5])
def test_real_detector_with_controlled_provider(client, engine, count):
    ids = [put_document(client, i, 600000 if i == 0 else 550000, page=i + 1) for i in range(count)]
    response = compare(client, ids)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["documents_analyzed"] == count
    assert data["document_ids"] == ids
    assert data["contradiction_count"] == 1
    assert engine.extractor.extract.await_count == count
    assert engine.verifier.verify_many.await_count == count
    claims, verifications, pages = engine.detector.detect.call_args.args
    assert len(claims) == len(verifications) == count
    assert [page.page_number for page in pages] == list(range(1, count + 1))
    assert [page.document_id for page in pages] == ids
    finding = data["contradictions"][0]
    assert finding["confidence"] is None
    assert finding["verification_status"] == "verified"
    assert finding["claim_a"]["evidence"]["document_id"] == ids[0]
    assert finding["claim_a"]["evidence"]["filename"] == "source-0.pdf"
    assert finding["claim_b"]["evidence"]["page_number"] == 2
    assert finding["claim_b"]["evidence"]["text"] == "Acme Ltd loan amount is INR 550000."


def test_comparison_persists_evidence_and_reuses_verified_inputs(client, engine, settings):
    ids = [put_document(client, i, amount) for i, amount in enumerate([600000, 550000])]
    result = compare(client, ids).json()
    url = f"{ROUTE}/{result['analysis_id']}"
    assert compare(client, ids).status_code == 200
    assert engine.extractor.extract.await_count == 2
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(url).json() == result
        evidence = restarted.get(f"{url}/evidence").json()
        claim = result["contradictions"][0]
        assert evidence["evidence"] == [{
            "finding_id": claim["id"], "source_a": claim["claim_a"]["evidence"],
            "source_b": claim["claim_b"]["evidence"],
        }]
        paths = restarted.get("/openapi.json").json()["paths"]
        assert "post" in paths[ROUTE]
        assert "get" in paths[f"{ROUTE}/{{analysis_id}}/evidence"]


@pytest.mark.parametrize("body", [
    [], [str(uuid4())], [str(uuid4()) for _ in range(6)], ["invalid", str(uuid4())],
    [str(UUID(int=1)), str(UUID(int=1))],
])
def test_validates_document_count_unique_ids_and_uuid(client, body):
    assert compare(client, body).status_code == 422


def test_missing_sources_and_analysis(client, engine):
    valid = put_document(client, 0, 600000)
    assert compare(client, [valid, str(uuid4())]).status_code == 404
    assert client.get(f"{ROUTE}/{uuid4()}").status_code == 404
    assert client.get(f"{ROUTE}/{uuid4()}/evidence").status_code == 404
    assert client.get(f"{ROUTE}/invalid").status_code == 422
    engine.extractor.extract.assert_not_awaited()


def test_ocr_documents_are_skipped(client, engine):
    scanned = put_document(client, 0)
    readable = put_document(client, 1, 600000)
    response = compare(client, [scanned, readable])
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "insufficient_readable_documents"
    assert result["ocr_required_document_ids"] == [scanned]
    engine.extractor.extract.assert_not_awaited()
    second = put_document(client, 2, 550000, page=3)
    result = compare(client, [scanned, readable, second]).json()
    assert result["documents_analyzed"] == 2
    assert result["ocr_required_document_ids"] == [scanned]
    assert result["contradictions"][0]["claim_b"]["evidence"]["page_number"] == 3


@pytest.mark.parametrize("issue", ["fabricated", "missing", "review", "time", "confidence", "duplicate"])
def test_detector_findings_are_checked_and_deduplicated(client, engine, issue):
    ids = [put_document(client, i, amount) for i, amount in enumerate([600000, 550000])]
    real_detect = engine.detector.detect.side_effect

    def mutate(*args):
        finding = real_detect(*args)[0]
        if issue == "fabricated":
            finding.source_2.snippet = "Made-up quote"
        elif issue == "missing":
            return [finding.model_copy(update={"source_2": None})]
        elif issue == "review":
            finding.requires_human_review = True
        elif issue == "time":
            finding.contradiction_type = "temporal_change"
        elif issue == "confidence":
            return [SimpleNamespace(**dict(finding), confidence=0.99)]
        return [finding, finding] if issue == "duplicate" else [finding]

    engine.detector.detect.side_effect = mutate
    result = compare(client, ids).json()
    if issue in {"duplicate", "confidence"}:
        assert result["contradiction_count"] == 1
        assert result["contradictions"][0]["confidence"] is None
    else:
        assert result["contradictions"] == []
        assert "excluded" in " ".join(result["warnings"])


def test_unverified_side_does_not_reach_detector(client, engine):
    ids = [put_document(client, i, amount) for i, amount in enumerate([600000, 550000])]
    real_verify = engine.verifier.verify_many.side_effect

    async def verify(claims, evidence):
        reports = await real_verify(claims, evidence)
        if evidence[0].document_id == ids[1]:
            reports[0].evidence = []
        return reports

    engine.verifier.verify_many.side_effect = verify
    result = compare(client, ids).json()
    assert result["contradictions"] == []
    assert len(engine.detector.detect.call_args.args[0]) == 1
    assert "lacked usable independent verification" in " ".join(result["warnings"])


def test_equivalent_values_produce_no_contradictions(client, engine):
    ids = [put_document(client, i, 600000) for i in range(2)]
    result = compare(client, ids).json()
    assert result["status"] == "completed"
    assert result["contradictions"] == []


def test_unconfigured_provider_returns_safe_503(client, monkeypatch):
    ids = [put_document(client, i, 600000) for i in range(2)]
    monkeypatch.setattr(ai_adapter, "inspect_engine", lambda: ai_adapter.unavailable("Provider unavailable."))
    response = compare(client, ids)
    assert response.status_code == 503
    assert response.json() == {"detail": "Provider unavailable."}


@pytest.mark.parametrize("exception_name, status", [("request_error", 503), ("unexpected", 500)])
def test_engine_errors_are_redacted(client, engine, exception_name, status):
    ids = [put_document(client, i, 600000) for i in range(2)]
    exception = engine.request_error if exception_name == "request_error" else RuntimeError
    engine.extractor.extract.side_effect = exception("private provider details")
    response = compare(client, ids)
    assert response.status_code == status
    assert "private" not in response.text


def test_comparison_times_out_without_saving_an_analysis(client, engine, monkeypatch):
    ids = [put_document(client, i, 600000) for i in range(2)]

    async def slow(*_):
        await asyncio.sleep(1)

    engine.extractor.extract.side_effect = slow
    monkeypatch.setattr(contradiction_service, "COMPARISON_TIMEOUT", 0.02)
    assert compare(client, ids).status_code == 504
    with client.app.state.database.sessions() as session:
        assert session.query(ContradictionAnalysis).count() == 0


def test_changed_or_deleted_source_invalidates_analyses(client, engine):
    ids = [put_document(client, i, amount) for i, amount in enumerate([600000, 550000])]
    analysis_id = compare(client, ids).json()["analysis_id"]
    url = f"{ROUTE}/{analysis_id}"
    database = client.app.state.database
    with database.sessions.begin() as session:
        page = session.get(DocumentPage, (UUID(ids[0]), 1))
        page.text = "Acme Ltd loan amount is INR 650000."
    assert client.get(url).status_code == 404
    refreshed = compare(client, ids).json()["analysis_id"]
    assert refreshed != analysis_id
    assert engine.extractor.extract.await_count == 3
    assert client.delete(f"/api/documents/{ids[0]}").status_code == 204
    assert client.get(f"{ROUTE}/{refreshed}/evidence").status_code == 404
    with database.sessions() as session:
        assert session.get(ContradictionAnalysis, UUID(analysis_id)) is None
        assert session.get(ContradictionAnalysis, UUID(refreshed)) is None
        assert session.get(VerifiedInputCache, UUID(ids[0])) is None


def test_source_change_during_inference_prevents_save(client, engine):
    ids = [put_document(client, i, amount) for i, amount in enumerate([600000, 550000])]
    original_detect = engine.detector.detect.side_effect
    database = client.app.state.database

    def detect(*args):
        findings = original_detect(*args)
        with database.sessions.begin() as session:
            page = session.get(DocumentPage, (UUID(ids[0]), 1))
            page.text = "Acme Ltd loan amount is INR 700000."
        return findings

    engine.detector.detect.side_effect = detect
    assert compare(client, ids).status_code == 409
    with database.sessions() as session:
        assert session.query(ContradictionAnalysis).count() == 0
