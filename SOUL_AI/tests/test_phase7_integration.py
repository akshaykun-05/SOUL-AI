import asyncio
import base64

import pymupdf
import pytest
from pydantic import ValidationError

from soul_ai.integration import EngineResult, PdfUpload, SOULAIEngineService
from soul_ai.integration.contracts import ResultStatus
from demos.rule_based_text_provider import RuleBasedTextProvider


def make_pdf(text):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def upload(doc_id, filename, sentence):
    return PdfUpload(document_id=doc_id, filename=filename,
                     content_base64=base64.b64encode(make_pdf(sentence)).decode())


def test_result_envelope_requires_data_or_error():
    with pytest.raises(ValidationError):
        EngineResult(request_id="r", status=ResultStatus.success)
    with pytest.raises(ValidationError):
        EngineResult(request_id="r", status=ResultStatus.error,
                     data={}, error={"code": "X", "message": "bad"})


def test_bad_upload_returns_consistent_error():
    service = SOULAIEngineService()
    result = service.extract_documents("r1", [PdfUpload.model_construct(
        document_id="d", filename="bad.pdf", content_base64="bm90IGEgcGRm")])
    assert result.status == ResultStatus.error
    assert result.error.code == "DOCUMENT_EXTRACTION_FAILED"
    assert result.data is None


def test_three_document_pipeline_extracts_and_reports_planted_conflict():
    service = SOULAIEngineService(claim_provider=RuleBasedTextProvider(),
                                  verifier_provider=RuleBasedTextProvider())
    uploads = [
        upload("doc-a", "agreement.pdf", "Acme Ltd loan amount is INR 600,000."),
        upload("doc-b", "bank-letter.pdf", "Acme Ltd loan amount is INR 550,000."),
        upload("doc-c", "copy.pdf", "Acme Ltd loan amount is INR 600,000."),
    ]
    extracted = service.extract_documents("extract", uploads)
    assert extracted.status == ResultStatus.success
    assert extracted.data["evidence_count"] == 3
    result = asyncio.run(service.answer("answer", "What is Acme Ltd loan amount?"))
    assert result.status == ResultStatus.success
    report = result.data
    assert report["status"] == "conflict"
    assert report["conflicts"]
    finding = report["conflicts"][0]
    assert {finding["source_1"]["filename"], finding["source_2"]["filename"]} == {
        "agreement.pdf", "bank-letter.pdf"}
    assert finding["source_1"]["page_number"] == finding["source_2"]["page_number"] == 1
    assert "INR 600,000" in finding["source_1"]["snippet"]
    assert "INR 550,000" in finding["source_2"]["snippet"]


def test_retrieval_and_empty_question_error_shape():
    service = SOULAIEngineService()
    result = service.retrieve_evidence("r", "loan amount")
    assert result.status == ResultStatus.success
    bad = service.retrieve_evidence("r2", " ")
    assert bad.status == ResultStatus.error
    assert bad.error.code == "INVALID_REQUEST"
