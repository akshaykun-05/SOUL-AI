import base64
import re

import pymupdf
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from demos.rule_based_text_provider import RuleBasedTextProvider
from soul_ai.integration.service import SOULAIEngineService


def make_pdf(text):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def make_app():
    provider = RuleBasedTextProvider()
    service = SOULAIEngineService(claim_provider=provider, verifier_provider=provider)
    return create_app(Settings(_env_file=None), service)


def test_upload_conflict_answer_and_original_pdf_citation():
    with TestClient(make_app()) as client:
        first_pdf = make_pdf("Acme Ltd loan amount is INR 600,000.")
        second_pdf = make_pdf("Acme Ltd loan amount is INR 550,000.")
        upload = client.post("/api/soul-ai/documents/extract", json={
            "request_id": "upload-1",
            "documents": [
                {"document_id": "doc-a", "filename": "agreement.pdf", "content_base64": base64.b64encode(first_pdf).decode()},
                {"document_id": "doc-b", "filename": "letter.pdf", "content_base64": base64.b64encode(second_pdf).decode()},
            ],
        })
        assert upload.status_code == 200
        assert upload.json()["data"]["evidence_count"] == 2

        answer = client.post("/api/soul-ai/answer", json={
            "request_id": "answer-1",
            "question": "What is Acme Ltd loan amount?",
            "document_ids": ["doc-a", "doc-b"],
        })
        result = answer.json()["data"]
        assert result["status"] == "conflict"
        assert {result["conflicts"][0]["source_1"]["filename"], result["conflicts"][0]["source_2"]["filename"]} == {
            "agreement.pdf", "letter.pdf"
        }
        assert result["conflicts"][0]["source_1"]["page_number"] == 1

        pdf_response = client.get("/api/soul-ai/documents/doc-a/file")
        assert pdf_response.status_code == 200
        assert pdf_response.headers["content-type"] == "application/pdf"
        assert pdf_response.content == first_pdf


def test_question_absent_from_uploaded_pdf_returns_insufficient_evidence():
    with TestClient(make_app()) as client:
        pdf = make_pdf("Acme Ltd loan amount is INR 600,000.")
        uploaded = client.post("/api/soul-ai/documents/extract", json={
            "request_id": "upload-2",
            "documents": [{"document_id": "doc-a", "filename": "agreement.pdf", "content_base64": base64.b64encode(pdf).decode()}],
        })
        assert uploaded.status_code == 200
        answer = client.post("/api/soul-ai/answer", json={
            "request_id": "answer-2",
            "question": "What is the registered office address?",
            "document_ids": ["doc-a"],
        })
        assert answer.json()["data"]["status"] == "insufficient_evidence"


def test_supported_answer_contains_source_quote_and_page():
    with TestClient(make_app()) as client:
        pdf = make_pdf("Acme Ltd loan amount is INR 600,000.")
        client.post("/api/soul-ai/documents/extract", json={
            "request_id": "upload-supported",
            "documents": [{"document_id": "doc-supported", "filename": "agreement.pdf", "content_base64": base64.b64encode(pdf).decode()}],
        })
        answer = client.post("/api/soul-ai/answer", json={
            "request_id": "answer-supported",
            "question": "What is Acme Ltd loan amount?",
            "document_ids": ["doc-supported"],
        })
        result = answer.json()["data"]
        assert result["status"] == "answered"
        assert result["claims"][0]["evidence"][0] == {
            "filename": "agreement.pdf", "page_number": 1,
            "text": "Acme Ltd loan amount is INR 600,000.",
        }


def test_invalid_pdf_returns_engine_error_without_storing_document():
    with TestClient(make_app()) as client:
        response = client.post("/api/soul-ai/documents/extract", json={
            "request_id": "upload-invalid",
            "documents": [{"document_id": "bad", "filename": "bad.pdf", "content_base64": base64.b64encode(b"not a PDF").decode()}],
        })
        assert response.status_code == 200
        assert response.json()["status"] == "error"
        assert response.json()["error"]["code"] == "DOCUMENT_EXTRACTION_FAILED"
        assert client.get("/api/soul-ai/documents/bad/file").status_code == 404


def test_missing_model_key_returns_error_instead_of_a_fabricated_answer():
    service = SOULAIEngineService()
    with TestClient(create_app(Settings(_env_file=None), service)) as client:
        pdf = make_pdf("Acme Ltd loan amount is INR 600,000.")
        client.post("/api/soul-ai/documents/extract", json={
            "request_id": "upload-no-provider",
            "documents": [{"document_id": "doc-a", "filename": "agreement.pdf", "content_base64": base64.b64encode(pdf).decode()}],
        })
        answer = client.post("/api/soul-ai/answer", json={
            "request_id": "answer-no-provider",
            "question": "What is Acme Ltd loan amount?",
            "document_ids": ["doc-a"],
        })
        assert answer.status_code == 503
        assert "provider" in answer.json()["detail"].lower()


def test_built_frontend_assets_and_client_routes_are_served_by_backend():
    with TestClient(create_app(Settings(_env_file=None), SOULAIEngineService())) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "/soul-ai-bridge.js" in index.text
        assert client.get("/soul-ai-bridge.js").status_code == 200
        asset_urls = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.text)
        assert len(asset_urls) >= 2

        for asset_url in asset_urls:
            asset = client.get(asset_url)
            assert asset.status_code == 200

        # BrowserRouter direct links and refreshes return the SPA shell.
        assert client.get("/verification").text == index.text
        missing_api = client.get("/api/not-a-route")
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")
