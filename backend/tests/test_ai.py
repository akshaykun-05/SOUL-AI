import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import ai_adapter
from app.ai_schemas import AIStatus
from app.config import Settings
from app.main import create_app
from test_documents import make_pdf, upload


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, upload_dir=tmp_path / "uploads",
        database_url=f"sqlite:///{(tmp_path / 'ai.sqlite3').as_posix()}",
        ai_cache_revision="test-model-v1",
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def engine(monkeypatch):
    class ProviderFailure(RuntimeError):
        pass

    async def extract(_question, evidence):
        return SimpleNamespace(status="answered", claims=[
            SimpleNamespace(
                claim_id="example-claim", claim_text="A fact on page three.",
                source_evidence_ids=[evidence[-1].evidence_id],
            )
        ])

    async def verify(claims, evidence):
        fact = evidence[-1]
        return [SimpleNamespace(
            claim_id=claims[0].claim_id, claim_text=claims[0].claim_text,
            status="SUPPORTED", requires_human_review=False,
            evidence=[SimpleNamespace(
                filename=fact.filename, page_number=fact.page_number,
                text=fact.extracted_text.strip(),
            )],
        )]

    engine = ai_adapter.Engine(
        SimpleNamespace, SimpleNamespace(extract=AsyncMock(side_effect=extract)),
        SimpleNamespace(verify_many=AsyncMock(side_effect=verify)), ProviderFailure,
        ("mock", "mock", None),
    )
    readiness = AIStatus(
        status="available", capabilities=ai_adapter.CAPABILITIES,
        package_ready=True, dependencies_ready=True, provider_configured=True,
    )
    monkeypatch.setattr(ai_adapter, "inspect_engine", lambda: (readiness, engine))
    return engine


def process(client, *pages):
    response = upload(client, make_pdf(*pages))
    assert response.status_code == 201
    document_id = response.json()["document_id"]
    url = f"/api/ai/process/{document_id}"
    return url, client.post(url)


def test_status_and_swagger(client, engine):
    status = client.get("/api/ai/status").json()
    assert status["status"] == "available"
    assert status["provider_configured"]
    assert "page_citations" in status["capabilities"]
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/api/ai/status"]
    assert "post" in paths["/api/ai/process/{document_id}"]
    engine.extractor.extract.assert_not_awaited()


def test_page_transfer_validated_references_and_cache(client, engine):
    url, response = process(client, "Page one.", "", "A fact on page three.")
    assert response.status_code == 200
    result = response.json()
    assert result["processing_status"] == "completed"
    assert result["page_count"] == 3
    assert result["unavailable_capabilities"] == ["cross_document_contradictions"]
    evidence = engine.extractor.extract.await_args.args[1]
    assert [page.page_number for page in evidence] == [1, 3]
    assert [page.extracted_text.strip() for page in evidence] == [
        "Page one.", "A fact on page three.",
    ]
    assert evidence[-1].document_id == result["document_id"]
    assert evidence[-1].end_offset == len(evidence[-1].extracted_text)
    assert engine.verifier.verify_many.await_args.args[1] == evidence
    reference = result["claims"][0]["evidence_references"][0]
    assert reference["evidence_id"] == evidence[-1].evidence_id
    assert reference["document_id"] == result["document_id"]
    assert reference["page_number"] == 3
    assert reference["text"] == "A fact on page three."
    assert client.post(url).json() == result
    engine.extractor.extract.assert_awaited_once()
    engine.model_identity = ("mock", "new-model", None)
    assert client.post(url).status_code == 200
    assert engine.extractor.extract.await_count == 2
    assert client.delete(f"/api/documents/{result['document_id']}").status_code == 204
    assert client.post(url).status_code == 404


def test_unknown_and_invalid_document_ids(client, engine):
    assert client.post(f"/api/ai/process/{uuid4()}").status_code == 404
    assert client.post("/api/ai/process/not-a-uuid").status_code == 422
    engine.extractor.extract.assert_not_awaited()


def test_missing_package_status_and_processing(client, monkeypatch):
    def missing(_module):
        raise ModuleNotFoundError("private/path", name="soul_ai")

    monkeypatch.setattr(ai_adapter, "import_module", missing)
    status = client.get("/api/ai/status").json()
    assert status["status"] == "unavailable"
    assert not status["package_ready"]
    _, response = process(client, "Readable")
    assert response.status_code == 503
    assert "private" not in response.text


def test_missing_dependency_status_and_processing(client, monkeypatch):
    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "soul_ai.claim_extraction":
            raise ModuleNotFoundError("private/path", name="optional_dep")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(ai_adapter, "import_module", lambda _: object())
    monkeypatch.setattr(builtins, "__import__", missing)
    status = client.get("/api/ai/status").json()
    assert status["package_ready"]
    assert not status["dependencies_ready"]
    _, response = process(client, "Readable")
    assert response.status_code == 503
    assert "private" not in response.text


def test_scanned_pdf_skips_engine(client, engine):
    _, response = process(client, "", "")
    assert response.status_code == 200
    assert response.json()["processing_status"] == "ocr_required"
    assert response.json()["claims"] is None
    engine.extractor.extract.assert_not_awaited()


@pytest.mark.parametrize("exception_type, expected", [(RuntimeError, 500), (ImportError, 503)])
def test_engine_errors_are_safe(client, engine, exception_type, expected):
    engine.extractor.extract.side_effect = exception_type("C:/private/key")
    _, response = process(client, "Readable")
    assert response.status_code == expected
    assert "private" not in response.text


def test_provider_failure_returns_unavailable(client, engine):
    engine.extractor.extract.side_effect = engine.request_error("private URL")
    _, response = process(client, "Readable")
    assert response.status_code == 503
    assert "private" not in response.text


def test_invalid_engine_output_is_rejected(client, engine):
    engine.verifier.verify_many.side_effect = None
    engine.verifier.verify_many.return_value = [SimpleNamespace(
        claim_id="example-claim", claim_text="A fact on page three.",
        status="SUPPORTED", requires_human_review=False,
        evidence=[SimpleNamespace(filename="report.pdf", page_number=1, text="Invented quote")],
    )]
    _, response = process(client, "Readable")
    assert response.status_code == 500
    assert "Invented" not in response.text


def test_abstention_returns_no_findings_and_is_not_cached(client, engine):
    engine.extractor.extract.side_effect = None
    engine.extractor.extract.return_value = SimpleNamespace(status="ambiguous", claims=[])
    url, response = process(client, "Readable")
    assert response.status_code == 200
    result = response.json()
    assert result["processing_status"] == "insufficient_evidence"
    assert result["claims"] == result["evidence_references"] == []
    assert client.post(url).status_code == 200
    assert engine.extractor.extract.await_count == 2
    engine.verifier.verify_many.assert_not_awaited()



@pytest.fixture
def provider_env(tmp_path, monkeypatch):
    from app.config import BACKEND_DIR

    # Exercise the real engine settings and provider factory without installing
    # or modifying the teammate package or making a network request.
    monkeypatch.syspath_prepend(str(BACKEND_DIR.parent / "SOUL_AI"))
    for name in ("PROVIDER", "API_KEY", "MODEL", "BASE_URL", "TEMPERATURE"):
        monkeypatch.delenv(f"SOUL_LLM_{name}", raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(ai_adapter, "ENV_FILE", env_file)
    other_dir = tmp_path / "other-working-directory"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    return env_file


@pytest.mark.parametrize("key_setting", ["# SOUL_LLM_API_KEY=", 'SOUL_LLM_API_KEY="   "'])
def test_provider_readiness_reports_missing_or_blank_key(client, provider_env, key_setting):
    provider_env.write_text(f"SOUL_LLM_PROVIDER=openai\n{key_setting}\n", encoding="utf-8")

    response = client.get("/api/ai/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["package_ready"] and body["dependencies_ready"]
    assert body["provider_configured"] is False
    assert body["api_key_present"] is False
    assert "SOUL_LLM_API_KEY is missing or blank" in body["diagnostics"][0]


@pytest.mark.parametrize("provider", ["openai", "openai-compatible"])
def test_provider_reads_dotenv_and_reports_only_key_presence(client, provider_env, provider):
    test_value = uuid4().hex
    provider_env.write_text(
        f"SOUL_LLM_PROVIDER={provider}\nSOUL_LLM_API_KEY={test_value}\n",
        encoding="utf-8",
    )

    response = client.get("/api/ai/status")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "available"
    assert body["provider_configured"] is True
    assert body["api_key_present"] is True
    leaked = test_value in response.text
    assert not leaked


def test_process_environment_overrides_dotenv(client, provider_env, monkeypatch):
    provider_env.write_text(
        f"SOUL_LLM_PROVIDER=openai\nSOUL_LLM_API_KEY={uuid4().hex}\n", encoding="utf-8",
    )
    monkeypatch.setenv("SOUL_LLM_PROVIDER", "unsupported")
    response = client.get("/api/ai/status").json()
    assert response["provider_configured"] is False
    assert response["api_key_present"] is True
    assert "openai-compatible" in response["diagnostics"][0]

    monkeypatch.setenv("SOUL_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SOUL_LLM_API_KEY", "")
    response = client.get("/api/ai/status").json()
    assert response["provider_configured"] is False
    assert response["api_key_present"] is False
