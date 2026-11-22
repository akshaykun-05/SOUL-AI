# SOUL AI - Phase 6

SOUL AI is an evidence-grounded document verification engine for Hack-A-Sol 5.0. It extracts page-aware PDF evidence, generates atomic claim candidates, independently verifies claims, detects cross-document conflicts, abstains when it cannot support an answer, and measures reliability with a reproducible 40-case benchmark.

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
Candidate claims -> separate verifier pass over all original evidence
        |
        v
Cross-document comparison of verified facts
        |
        v
Citation-grounded answer or explicit abstention
        |
        v
Separate honesty benchmark and dashboard metrics
        |
        v
Versioned JSON contract -> separate FastAPI backend / React frontend
```

Implemented phases:

- `schemas/` contains Pydantic models for all core evidence objects.
- `api/contracts.py` defines the backend-facing request and response models.
- `providers/base.py` defines a provider-independent asynchronous LLM interface.
- `config.py` reads model settings from environment variables.
- `extraction/pdf.py` ingests PDFs, stores paragraph text, page offsets and available bounding boxes, flags sparse/empty pages, and optionally falls back to Tesseract OCR.
- `claim_extraction/extractor.py` sends only the question and supplied evidence to an injected `LLMProvider`, validates its JSON response, checks cited evidence IDs, and conservatively rejects candidate details not found in cited text.
- `verification/verifier.py` runs one dedicated verifier call per claim over every original evidence chunk. Candidate source IDs are not passed to the verifier. It validates filename, page, and each quoted snippet against original extracted text before returning citations.
- `contradictions/detector.py` compares only `SUPPORTED` claims with checked source quotes. It matches normalized entities and attributes, normalizes common currencies, scaled numbers, percentages, dates, and payment periods, and returns both source snippets.
- `response_engine/engine.py` coordinates lexical retrieval, candidate claim extraction, independent verification, per-document conflict checks, citation assembly, and abstention. It returns only verified claim text, not free-form candidate answer prose.
- `evaluation/benchmark.py` scores outcomes, target-related claim verification trace entries, and citations against separate gold labels. Internal evaluation traces are excluded from normal response JSON.
- `benchmark/data/inputs.json` stores the 40 model inputs. `benchmark/data/labels.json` stores expected outcomes and source facts separately; the runner passes only each question and its evidence to the engine factory.
- `benchmark/run.py` runs the configured engine factory, writes per-case JSON and computed dashboard metrics, and records dataset hashes. The runner contains no hardcoded success scores.
- Without an engine factory, the runner writes `not_run` with null metrics rather than substituting a scripted model or inventing results.
- `InMemoryEvidenceIndex` provides deterministic indexed keyword retrieval for local use. Production can implement the `EvidenceIndex` protocol with a vector or hybrid index.
- If no evidence is retrieved, an important claim is unsupported, verification needs review, or a document-level scan is ambiguous, the engine abstains with: `Insufficient evidence. The uploaded documents do not contain enough information to answer this question.`
- Conflicting verified facts are returned together with both source citations; neither value is selected silently.
- Equivalent periodic amounts (for example, 600,000 annually and 50,000 monthly) are normalized. Cross-currency amounts are not converted without a date-appropriate exchange rate; those findings require human review.
- Supply known document version labels to distinguish a possible revision from a same-version conflict. Findings across versions are labeled `version_change` and require human review. Different temporal contexts are labeled `temporal_change`.
- Ambiguous date formats, incompatible units, and differing contractual conditions require human review rather than being presented as certain contradictions.
- Verification statuses are `SUPPORTED`, `CONTRADICTED`, and `UNSUPPORTED`. Invalid citations and low/unknown-confidence OCR cannot produce a definitive result; ambiguous evidence requires human review.
- Numeric values and explicit currencies are compared deterministically before accepting a `SUPPORTED` result, including common scales such as lakh/lac and crore.
- Claim extraction returns `answered`, `insufficient_evidence`, or `ambiguous`. Unanswered/ambiguous responses contain no claims.
- Evidence IDs on claims are candidate sources, not proof. Every claim still requires independent verification.
- OCR is opt-in and requires the Tesseract system executable. Missing OCR dependencies are reported as warnings; they never produce fabricated evidence.
- The verifier uses a dedicated injected `LLMProvider` instance; configure it separately from the answer/claim extraction provider in the application. Frontend and backend-owned application code are not implemented in this phase.

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
│   ├── verification/
│   │   ├── __init__.py
│   │   └── verifier.py
│   ├── contradictions/
│   │   ├── __init__.py
│   │   └── detector.py
│   ├── response_engine/
│   │   ├── __init__.py
│   │   └── engine.py
│   ├── evaluation/
│   │   ├── __init__.py
│   │   └── benchmark.py
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
    ├── test_claim_extraction.py
    ├── test_claim_verification.py
    ├── test_contradictions.py
    ├── test_response_engine.py
    └── test_benchmark.py
benchmark/
├── create_dataset.py
├── run.py
├── data/
│   ├── inputs.json
│   └── labels.json
└── results/
    └── latest.json
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

Each accepted claim includes `claim_id`, `claim_text`, `entity`, `attribute`, `value`, `unit`, `temporal_context`, and `source_evidence_ids`. A lexical check guards against details missing from cited text and rejects claims that bundle multiple detected numeric/date facts. That check is deliberately conservative and does not establish that a claim is true; independent verification is handled in Phase 3.

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

### Independent claim verification

Use a verifier-specific provider instance, then pass the original Phase 1 evidence collection. The verifier ignores candidate evidence IDs and receives all source text so it can locate support or conflict independently.

```python
from soul_ai.verification import ClaimVerifier

reports = await ClaimVerifier(verifier_provider).verify_many(
    claims=claim_result.claims,
    original_evidence=all_extracted_evidence,
)
```

Every returned quote must be an exact substring of extracted source text with the same filename and page. A supported numeric claim must also match the cited number after deterministic normalization. Ambiguous claims, unverifiable citations, and low-confidence OCR return `UNSUPPORTED`; the latter two require human review.

Sample report:

```json
{
  "claim_id": "C001",
  "claim_text": "The loan amount is Rs. 5 lakh",
  "status": "SUPPORTED",
  "evidence": [
    {
      "filename": "loan_agreement.pdf",
      "page_number": 3,
      "text": "The sanctioned loan amount is Rs. 5,00,000."
    }
  ],
  "reason": "The source explicitly confirms the amount.",
  "requires_human_review": false
}
```

### Cross-document contradiction detection

Pass Phase 2 claim candidates, Phase 3 verification reports, and the original evidence. Only independently `SUPPORTED` claims with citations that still match the original evidence are compared. Optional document version labels are keyed by document ID.

```python
from soul_ai.contradictions import ContradictionDetector

findings = ContradictionDetector().detect(
    claims=claim_result.claims,
    verifications=verification_reports,
    original_evidence=all_extracted_evidence,
    document_versions={"doc-v1": "v1", "doc-v2": "v2"},
)
```

Sample finding:

```json
{
  "contradiction_id": "<generated UUID>",
  "entity": "Acme Ltd",
  "attribute": "loan amount",
  "value_1": "Rs. 6,00,000",
  "value_2": "Rs. 5,00,000",
  "source_1": {
    "filename": "agreement.pdf",
    "page_number": 2,
    "snippet": "The loan amount is Rs. 6,00,000.",
    "document_id": "doc-v1",
    "document_version": "v1",
    "temporal_context": null
  },
  "source_2": {
    "filename": "revised_agreement.pdf",
    "page_number": 3,
    "snippet": "The loan amount is Rs. 5,00,000.",
    "document_id": "doc-v2",
    "document_version": "v2",
    "temporal_context": null
  },
  "contradiction_type": "version_change",
  "explanation": "The values differ between document versions 'v1' and 'v2'; this may be an intentional revision.",
  "requires_human_review": true
}
```

### Zero-guessing response pipeline

Index original Phase 1 evidence, then inject the Phase 2 claim extractor and Phase 3 independent verifier. The engine answers with claim-level citations only after every candidate claim is supported. It emits the fixed insufficient-evidence message if it cannot verify the answer, and lists both sides of detected conflicts.

```python
from soul_ai.response_engine import InMemoryEvidenceIndex, ZeroGuessingEngine

index = InMemoryEvidenceIndex(all_extracted_evidence)
engine = ZeroGuessingEngine(
    retriever=index,
    claim_extractor=claim_extractor,
    verifier=independent_verifier,
)
response = await engine.answer(
    "What is the sanctioned loan amount?",
    document_versions={"loan-v1": "v1", "loan-v2": "v2"},
)
payload = response.model_dump(mode="json")
```

Tests exercise answerable, missing, conflicting, multi-source, unsupported, and misleading-assumption cases. They measure the pipeline behavior with controlled provider responses; they do not imply hallucinations are impossible or measure live model accuracy.

## Phase 6 honesty benchmark

The synthetic dataset has exactly 40 cases: 20 unsupported questions, five numerical contradictions, five date contradictions, five identity conflicts, and five consistent facts. Inputs and gold labels are stored separately. The benchmark uses text-backed `PdfEvidence` so it evaluates retrieval and response behavior without adding PDF parsing variability.

Validate the dataset counts:

```powershell
python -m benchmark.run --validate-only
```

Run with a model-backed engine factory. The factory receives only the case's `list[PdfEvidence]` and returns a configured `ZeroGuessingEngine`; it does not receive gold labels or expected outcomes.

```powershell
$env:SOUL_BENCHMARK_ENGINE_FACTORY = "my_app.benchmark_factory:create_engine"
python -m benchmark.run --output benchmark/results/latest.json
```

The factory must build its evidence index from the supplied evidence and configure the real answer and independent verifier providers. Each run records the input and label hashes, per-case predictions, errors, and dashboard metrics.

Metrics are computed from observed outputs: rejection rate over the 20 unsupported cases; source-pair-level contradiction precision and recall; case-level false-positive contradiction rate; claim verification accuracy over target-related audit observations; citation accuracy over emitted citations; and gold citation coverage. Claim-verification accuracy excludes unrelated extracted facts because the dataset does not label them. Metric tests validate the calculations; they are not presented as model benchmark results.

See [benchmark/results/latest.json](benchmark/results/latest.json) for the latest execution status.

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

## Phase 7 integration layer

`SOULAIEngineService` in `soul_ai.integration` is the backend-facing interface.
It owns an in-memory evidence index for a process and exposes `extract_documents`,
`retrieve_evidence`, `extract_claims`, `verify_claims`, `detect_contradictions`,
`answer`, and `evaluate_benchmark`. Extraction/retrieval/contradiction operations
are synchronous; model-backed operations are async. Each returns the same JSON
envelope: `{request_id, status, data, error}`. Success has `data` and null
`error`; failure has null `data` and `{code,message,retryable}`. Detailed result
schemas remain the Phase 1–6 Pydantic models; generated JSON Schema is available
with `Model.model_json_schema()` and upload contract via `PdfUpload`.

The optional FastAPI router is mounted with `app.include_router(create_router(service))`
from `soul_ai.integration.router`. This adds `/soul-ai/documents/extract` and
`/soul-ai/answer`; other operations are available as service calls. The engine
does not own or modify the main backend or React application.

### Backend request and response

PDFs use base64 JSON in this integration contract (a backend may instead call
`PdfIngestor` with raw bytes and persist evidence itself):

```json
{"request_id":"req-001","documents":[{"document_id":"doc-001","filename":"agreement.pdf","content_base64":"JVBERi0x..."}]}
```

Successful extraction data contains `documents` (each includes the original
document ID, filename, digest, page count, extraction status and evidence) and
`evidence_count`. Each evidence item preserves `evidence_id`, `document_id`,
`filename`, 1-based `page_number`, exact `extracted_text`, offsets, optional
bounding box, extraction method, and optional OCR confidence. The response
endpoint accepts:

```json
{"request_id":"req-002","question":"What is the loan amount?","document_ids":["doc-001"]}
```

The answer response's `data` is the `ZeroGuessingResponse`: `status` is
`answered`, `insufficient_evidence`, or `conflict`; citations in supported
claims include verbatim snippet, filename, and page. Conflict results include
both source records. Example frontend object:

```json
{"request_id":"req-002","status":"success","data":{"status":"conflict","answer":"The uploaded documents contain conflicting information: ...","claims":[],"conflicts":[{"entity":"Acme Ltd","attribute":"loan amount","value_1":"INR 600,000 INR","value_2":"INR 550,000 INR","source_1":{"filename":"agreement.pdf","page_number":1,"snippet":"Acme Ltd loan amount is INR 600,000.","document_id":"doc-001","document_version":null,"temporal_context":null},"source_2":{"filename":"letter.pdf","page_number":1,"snippet":"Acme Ltd loan amount is INR 550,000.","document_id":"doc-002","document_version":null,"temporal_context":null},"contradiction_type":"amount_conflict","explanation":"Both documents support different values for the same entity and attribute.","requires_human_review":false}],"requires_human_review":false},"error":null}
```

Supported-answer `data` example (wrapped in the same `request_id/status/data/error`
envelope):

```json
{"status":"answered","answer":"Acme Ltd loan amount is INR 600,000. [agreement.pdf, p. 1]","claims":[{"claim_id":"claim-id","claim_text":"Acme Ltd loan amount is INR 600,000.","evidence":[{"filename":"agreement.pdf","page_number":1,"text":"Acme Ltd loan amount is INR 600,000."}]}],"conflicts":[],"requires_human_review":false}
```

An operation error is returned as `{"request_id":"req-003","status":"error","data":null,"error":{"code":"DOCUMENT_EXTRACTION_FAILED","message":"Input is not a readable PDF","retryable":false}}`.

### Provider setup and limitations

The built-in adapter calls an OpenAI-compatible Chat Completions API using the
Python standard library. Configure `SOUL_LLM_PROVIDER`, `SOUL_LLM_MODEL`,
`SOUL_LLM_API_KEY`, and optionally `SOUL_LLM_BASE_URL` in the environment (or
local `.env`); never commit credentials. The claim extractor and verifier are
separate provider instances. Other model vendors can implement the small
`LLMProvider.generate(LLMRequest) -> LLMResponse` protocol and be injected.
Production retrieval is currently deterministic lexical matching in memory,
not semantic/vector retrieval. Persisted indexes and authentication/rate limits
belong in the owning backend. OCR requires the optional Python packages and a
system Tesseract executable. The deterministic provider in `demos/` only
understands the synthetic `Entity loan amount is INR number.` format; it is a
reproducibility aid, not a general-purpose language model or production provider.

### Reproducible three-document end-to-end demo

From the `SOUL_AI` directory with the project dependencies installed:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python demos/phase7_three_documents.py
```

The command creates three PDFs in memory, extracts their actual text, runs the
claim extraction and independent verification passes, and prints the detected
INR 600,000 versus INR 550,000 conflict with both filenames and page 1. Evidence
IDs and contradiction IDs are generated for each run. This local run verifies
the integration path only; it does not measure general model quality. To run
the benchmark using a configured model-backed factory, use the Phase 6 command
and dataset instructions in `benchmark/README.md`.

### Integration test results

Run `pytest` from this directory. The Phase 7 slice can be run alone with
`pytest tests/test_phase7_integration.py -q`; the complete suite validates all
existing phases plus the service envelope and three-PDF conflict flow.
