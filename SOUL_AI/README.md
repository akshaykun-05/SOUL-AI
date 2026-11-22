# SOUL AI - Phase 0

SOUL AI is an evidence-grounded document verification engine for Hack-A-Sol 5.0. This Phase 0 repository establishes the Python package, architecture, validated schemas, and JSON contracts. It does not implement extraction or verification behavior yet.

## Architecture overview

```text
Uploaded documents
        |
        v
Document ingestion adapter (future phase)
        |
        v
PyMuPDF text extraction -> page-preserving evidence spans
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

Phase 0 boundaries:

- `schemas/` contains Pydantic models for all core evidence objects.
- `api/contracts.py` defines the backend-facing request and response models.
- `providers/base.py` defines a provider-independent asynchronous LLM interface.
- `config.py` reads model settings from environment variables.
- No extraction, verification, frontend, or backend-owned application code is implemented.

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
    └── test_contracts.py
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
