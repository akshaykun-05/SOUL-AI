# SOUL AI - Phase 1

SOUL AI is an evidence-grounded document verification engine for Hack-A-Sol 5.0. Phase 1 adds page-aware PDF ingestion and evidence extraction. Claim decomposition and verification remain future phases.

## Architecture overview

```text
Uploaded documents
        |
        v
PDF upload (bytes, stream, or path)
        |
        v
PyMuPDF text extraction -> page-preserving paragraph evidence
        | (optional, scanned pages)
        v
Tesseract OCR -> confidence-scored evidence
        |
        v
Claim decomposition -> atomic claims
        |
        v
Independent claim verification + contradiction detection
        |
        v
Versioned JSON contract -> separate FastAPI backend / React frontend
```

Phase 1 boundaries:

- `schemas/` contains Pydantic models for all core evidence objects.
- `api/contracts.py` defines the backend-facing request and response models.
- `providers/base.py` defines a provider-independent asynchronous LLM interface.
- `config.py` reads model settings from environment variables.
- `extraction/pdf.py` ingests PDFs, stores paragraph text, page offsets and available bounding boxes, flags sparse/empty pages, and optionally falls back to Tesseract OCR.
- OCR is opt-in and requires the Tesseract system executable. Missing OCR dependencies are reported as warnings; they never produce fabricated evidence.
- Claim decomposition, verification, frontend, and backend-owned application code are not implemented in this phase.

## Folder structure

```text
SOUL_AI/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── pyproject.toml
├── soul_ai/
│   ├── __init__.py
│   ├── config.py
│   ├── extraction/
│   │   ├── __init__.py
│   │   └── pdf.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── contracts.py
│   ├── providers/
│   │   ├── __init__.py
│   │   └── base.py
│   └── schemas/
│       ├── __init__.py
│       ├── document.py
│       ├── evidence.py
│       ├── claims.py
│       ├── verification.py
│       └── contradiction.py
└── tests/
    ├── test_schemas.py
    ├── test_contracts.py
    └── test_pdf_extraction.py
```

## PDF ingestion

```python
from soul_ai.extraction import PdfIngestor

result = PdfIngestor(enable_ocr=False).ingest(
    uploaded_pdf_bytes,
    document_id="doc-001",
    filename="agreement.pdf",
)
payload = result.model_dump(mode="json")
```

Each evidence item contains a UUID, document and filename identity, 1-based page, extracted text, page text offsets, optional PDF-point bounding box, extraction method, and optional OCR confidence. `status` is `complete`, `low_confidence`, or `empty`; sparse pages and OCR problems appear in `warnings`.

To enable OCR, install the Python extras and the Tesseract executable for your operating system:

```powershell
pip install -e ".[ocr]"
```

OCR confidence is the mean Tesseract word confidence normalized to `[0, 1]`. OCR evidence bounding boxes currently cover the page because OCR is grouped into paragraphs; digital evidence uses the matching PyMuPDF text block box when available.

### Sample extraction JSON

```json
{
  "document_id": "doc-001",
  "filename": "agreement.pdf",
  "sha256": "<sha256 of uploaded PDF bytes>",
  "page_count": 1,
  "status": "complete",
  "evidence": [
    {
      "evidence_id": "<generated UUID>",
      "document_id": "doc-001",
      "filename": "agreement.pdf",
      "page_number": 1,
      "extracted_text": "The contract starts on 1 January 2025.",
      "start_offset": 0,
      "end_offset": 38,
      "bounding_box": [72.0, 59.1, 271.2, 75.6],
      "extraction_method": "text",
      "confidence": null
    }
  ],
  "warnings": []
}
```

## JSON contract

### Request

```json
{
  "request_id": "req-001",
  "question": "What is the effective date?",
  "document_ids": ["doc-001"]
}
```

### Successful or rejected response

```json
{
  "request_id": "req-001",
  "status": "completed",
  "answer": "The effective date is 1 January 2025.",
  "documents": [],
  "claims": [],
  "verifications": [],
  "contradictions": [],
  "rejected_reason": null,
  "error": null
}
```

Rejection is represented by `status: "rejected"` and a non-empty `rejected_reason`. Runtime failures use `status: "error"` and `error`. Every supported claim must carry evidence with a filename and 1-based `page_number`.

### Core schema names

- `DocumentMetadata`: document identity, digest, size, MIME type, pages, and upload time.
- `ExtractedEvidence`: document/page-bound supporting text and optional character offsets.
- `AtomicClaim`: one independently verifiable statement.
- `VerificationResult`: claim status, confidence, evidence, and rationale.
- `Contradiction`: conflicting claim and evidence references with severity.

## Installation

Windows PowerShell:

```powershell
cd "C:\Users\PC\OneDrive\Desktop\hack-a-sol\SOUL_AI"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Never place API credentials in source files or commit `.env`.

## Run tests

```powershell
pytest
```
