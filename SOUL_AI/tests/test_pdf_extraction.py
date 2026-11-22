from __future__ import annotations

import pymupdf as fitz

import pytest

from soul_ai.extraction import (
    ExtractionMethod,
    ExtractionStatus,
    PdfIngestor,
    PdfIngestionError,
)


def make_pdf(*, text: str | None = None, scanned: bool = False) -> bytes:
    output = fitz.open()
    page = output.new_page()
    if text:
        page.insert_text((72, 72), text, fontsize=12)
    if scanned:
        source = fitz.open()
        source_page = source.new_page()
        source_page.insert_text((72, 72), "Scanned evidence from a PDF document.", fontsize=14)
        pixmap = source_page.get_pixmap()
        page.insert_image(page.rect, pixmap=pixmap)
        source.close()
    data = output.tobytes()
    output.close()
    return data


def test_digital_pdf_preserves_page_text_offsets_and_geometry():
    result = PdfIngestor().ingest(
        make_pdf(text="The contract starts on 1 January 2025."),
        document_id="doc-1",
        filename="contract.pdf",
    )

    assert result.status == ExtractionStatus.complete
    assert result.evidence
    item = result.evidence[0]
    assert item.document_id == "doc-1"
    assert item.filename == "contract.pdf"
    assert item.page_number == 1
    assert item.extracted_text == "The contract starts on 1 January 2025."
    assert item.end_offset - item.start_offset == len(item.extracted_text)
    assert item.bounding_box is not None
    assert item.extraction_method == ExtractionMethod.text


def test_empty_pdf_is_flagged_without_fabricated_evidence():
    result = PdfIngestor().ingest(make_pdf(), document_id="empty-doc")

    assert result.status == ExtractionStatus.empty
    assert result.evidence == []
    assert "scanned" in result.warnings[0]


def test_scanned_pdf_uses_ocr_and_records_confidence(monkeypatch):
    monkeypatch.setattr(
        PdfIngestor,
        "_ocr_page",
        staticmethod(lambda page: ("OCR recovered source text.", 0.91)),
    )
    result = PdfIngestor(enable_ocr=True).ingest(
        make_pdf(scanned=True), document_id="scan-doc", filename="scan.pdf"
    )

    assert result.status == ExtractionStatus.complete
    assert len(result.evidence) == 1
    assert result.evidence[0].extraction_method == ExtractionMethod.ocr
    assert result.evidence[0].confidence == 0.91
    assert result.evidence[0].page_number == 1


def test_low_confidence_ocr_is_explicitly_flagged(monkeypatch):
    monkeypatch.setattr(
        PdfIngestor,
        "_ocr_page",
        staticmethod(lambda page: ("Uncertain text.", 0.12)),
    )
    result = PdfIngestor(enable_ocr=True).ingest(
        make_pdf(scanned=True), document_id="scan-doc"
    )

    assert result.status == ExtractionStatus.low_confidence
    assert result.evidence[0].confidence == 0.12
    assert any("below threshold" in warning for warning in result.warnings)


def test_invalid_pdf_is_rejected_with_clear_error():
    with pytest.raises(PdfIngestionError, match="not a readable PDF"):
        PdfIngestor().ingest(b"not a pdf", document_id="bad-doc")
