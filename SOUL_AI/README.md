# SOUL AI - Phase 2

SOUL AI is an evidence-grounded document verification engine for Hack-A-Sol 5.0. Phase 1 adds page-aware PDF evidence extraction. Phase 2 turns retrieved evidence into conservative atomic claim candidates using the configurable LLM provider interface. Independent verification remains a future phase.

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
Relevant evidence + question -> atomic claim extraction
        |
        v
Candidate claims -> independent verification (future phase)
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
- `claim_extraction/extractor.py` sends only the question and supplied evidence to an injected `LLMProvider`, validates its JSON response, checks cited evidence IDs, and conservatively rejects candidate details not found in cited text.
- Claim extraction returns `answered`, `insufficient_evidence`, or `ambiguous`. Unanswered/ambiguous responses contain no claims.
- Evidence IDs on claims are candidate sources, not proof. Every claim still requires independent verification.
- OCR is opt-in and requires the Tesseract system executable. Missing OCR dependencies are reported as warnings; they never produce fabricated evidence.
- Verification, frontend, and backend-owned application code are not implemented in this phase.

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
│   ├── claim_extraction/
│   │   ├── __init__.py
│   │   └── extractor.py
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
    ├── test_pdf_extraction.py
    └── test_claim_extraction.py
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

### Atomic claim extraction

Inject any implementation of the provider-independent `LLMProvider` interface. The provider must return a JSON object containing `status`, `answer`, `claims`, and `reason`. Claim extraction validates the response and only accepts candidate evidence IDs from the supplied evidence collection.

```python
from soul_ai.claim_extraction import ClaimExtractor

result = await ClaimExtractor(provider).extract(
    "When does the agreement expire?",
    retrieved_evidence,
)
```

Each accepted claim includes `claim_id`, `claim_text`, `entity`, `attribute`, `value`, `unit`, `temporal_context`, and `source_evidence_ids`. A lexical check guards against details missing from cited text and rejects claims that bundle multiple detected numeric/date facts. That check is deliberately conservative and does not establish that a claim is true; independent verification belongs to the next phase.

Example result:

```json
{
  "status": "answered",
  "answer": "The agreement expires on 30 June 2027.",
  "claims": [
    {
      "claim_id": "<generated UUID>",
      "claim_text": "The agreement expires on 30 June 2027.",
      "entity": "agreement",
      "attribute": "expiration date",
      "value": "30 June 2027",
      "unit": null,
      "temporal_context": "30 June 2027",
      "source_evidence_ids": ["ev-001"]
    }
  ],
  "reason": null
}
```

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
