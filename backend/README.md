# SOUL AI Backend

Use Python 3.10 or later. From the repository root in PowerShell:

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8765 --reload
```

Open [Swagger UI](http://127.0.0.1:8765/docs) or check [service health](http://127.0.0.1:8765/api/health).

## Configuration

Copy `.env.example` to `.env` in `backend/` to override the defaults. Process environment variables take precedence over the file. CORS allows localhost and 127.0.0.1 on ports 3000 and 5173 by default. Override it with a JSON array in `SOUL_BACKEND_CORS_ORIGINS`.

| Setting | Default | Behavior |
| --- | --- | --- |
| `SOUL_BACKEND_UPLOAD_DIR` | `uploads` | PDF storage directory. Relative paths resolve from `backend/`. |
| `SOUL_BACKEND_MAX_UPLOAD_SIZE_MB` | `10` | Maximum file size in MiB. |
| `SOUL_BACKEND_MAX_FILES_PER_UPLOAD` | `5` | Maximum batch size, configurable from 1 to 5. |
| `SOUL_BACKEND_DATABASE_URL` | `sqlite:///data/soul_ai.sqlite3` | File-backed SQLite URL. Relative paths resolve from `backend/`. On Windows, an absolute URL looks like `sqlite:///C:/data/soul_ai.sqlite3`. |

These four settings also accept their unprefixed names, including `DATABASE_URL`. Keep custom database and upload paths under `backend/` or outside the checkout. The backend ignore rules cover SQLite files and sidecars, generated document directories, and local secrets.

Startup creates the database directory and tables if needed. SQLite stores document metadata and ordered page text. Original PDFs stay on disk in UUID directories. Keep the database and uploads together when backing up or moving the service.

## Import Phase 2 documents

Stop the backend and use the same database and upload settings for migration and startup. From `backend/`, run:

```powershell
.\.venv\Scripts\python.exe -m app.migrate_documents
```

The command creates the tables if needed and imports each valid legacy `metadata.json` and its page text into SQLite. The PDF remains in its original directory. The PDF's modification time supplies the initial timestamps because Phase 2 metadata has no timestamps. The command preserves source files and reports imported, duplicate, missing, and invalid records. You can rerun it safely to import records that were skipped after fixing their source files. Start the backend to access the imported documents through the API.

## Document endpoints

| Endpoint | Behavior |
| --- | --- |
| `POST /api/documents/upload` | Send one PDF in the multipart field `file`. HTTP 201 returns metadata and extracted pages. |
| `POST /api/documents/upload-multiple` | Repeat multipart field `files`. HTTP 200 returns per-file results in input order. Successful uploads persist independently. |
| `GET /api/documents` | Returns `items`, `total`, `limit`, and `offset`. Items are newest first and omit page text. |
| `GET /api/documents/{document_id}` | Returns stored metadata and extracted pages. |
| `GET /api/documents/{document_id}/file` | Downloads the original PDF as an attachment. |
| `DELETE /api/documents/{document_id}` | Removes the document, extracted pages, and its stored directory. Returns HTTP 204. |

List parameters are `limit` (default 20, maximum 100), `offset` (default 0), `extraction_status` (`extracted` or `ocr_required`), and `search` (a literal filename substring with SQLite's case folding). The filtered count is computed before pagination. Unknown UUIDs return HTTP 404. Invalid UUIDs and invalid list parameters return HTTP 422.

Malformed multipart requests or requests with too many files are rejected before storage. The aggregate request cap is the allowed file count times the per-file limit plus 1 MiB for multipart overhead. Requests over this cap return HTTP 413. Within the cap, each file is checked independently. File errors use HTTP 415 for an unsupported file, HTTP 422 for an empty, corrupt, or password-protected PDF, and HTTP 413 for an oversized PDF.

Uploads stage files before publishing and remove them if the database write fails. Deletion moves the document directory aside while the database transaction is pending and restores it on rollback. An abrupt termination or a filesystem cleanup failure can leave files that need manual reconciliation. Documents without extractable text are flagged for OCR. The local API has no authentication and serves original PDF bytes unchanged. OCR and AI inference remain outside this phase.

## Tests

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The backend tests use temporary SQLite databases and upload directories.

## AI processing

From `backend/`, install the optional teammate engine in the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ..\SOUL_AI
```

For a read-only checkout, use the source directory on the Python import path instead:

```powershell
$env:PYTHONPATH = (Resolve-Path ..\SOUL_AI).Path
```

The backend can start and manage documents without the engine. For model processing, set `SOUL_LLM_PROVIDER` (`openai` or `openai-compatible`), `SOUL_LLM_MODEL`, and `SOUL_LLM_API_KEY` in the process environment or backend `.env`. Set `SOUL_LLM_BASE_URL` for a compatible endpoint. The engine's HTTP provider uses the standard library and requires a nonblank key. Readiness checks import the engine and construct the provider locally. Processing sends stored readable text and filename/page references to the configured provider, so use a provider authorized to receive the document text.

### Engine interfaces used

| Engine interface | Backend behavior |
| --- | --- |
| `PdfEvidence` | Wraps stored SQLite text as one evidence span per readable page. Page numbers and text offsets are preserved, and blank pages are skipped. |
| `ClaimExtractor.extract(question, evidence)` | Runs the async extraction pass with a fixed document-wide fact prompt. |
| `ClaimVerifier.verify_many(claims, original_evidence)` | Runs a separate async verification pass with its own provider instance. Only supported claims with checked quotations appear in the API result. |
| `ContradictionDetector.detect(...)` | Detected by readiness checks. It compares different source documents, so the single-document route reports cross-document contradictions as unavailable. |
| `PdfIngestor`, `SOULAIEngineService`, `ZeroGuessingEngine` | The engine also provides PDF ingestion (with optional Tesseract OCR), an in-memory service, and a question-answer pipeline. This integration uses the existing SQLite repository and accepts stored text directly. |

The status endpoint checks the package, required imports and local provider configuration without making a model request. The status response includes api_key_present, which is true for a nonblank key, false for a missing or blank key, and null when the settings cannot be checked. Process variables override backend/.env, including empty values. An available status means locally configured. Use your own provider key and keep it in the ignored backend/.env or the process environment. Processing checks provider reachability and credentials when it calls the provider. Backend tests mock engine calls and require no provider credentials or model downloads.

### Try a real PDF

Start Uvicorn from `backend/`, then run these commands in another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/ai/status
$upload = curl.exe -fsS -F "file=@C:/path/to/document.pdf" http://127.0.0.1:8765/api/documents/upload | ConvertFrom-Json
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/api/ai/process/$($upload.document_id)"
```

Processing returns `completed` for supported findings, `insufficient_evidence` when no independently supported claims were produced, or `ocr_required` for documents without readable text. Every returned citation contains the original document UUID, filename, 1-based page number and verbatim excerpt. Unknown documents return HTTP 404, malformed UUIDs return 422, unavailable engine or provider returns 503, and unexpected failures return a generic 500. The single-document route does not perform OCR, cross-document comparison, or Q&A. The fixed prompt is aimed at identifying explicit facts and provides no guarantee of exhaustive extraction. Large documents are limited by the configured model's context window.

### Optional SQLite result cache

Set `SOUL_BACKEND_AI_CACHE_REVISION` to a nonempty deployment revision to reuse fully verified results. Change the revision when deploying a new engine build, changing provider credentials or routing, or updating a model behind the same name. Cache keys also include the stored pages and metadata, adapter schema version, prompt, and configured provider/model/URL. OCR, abstentions, partial verification and errors are excluded. Deleting a document deletes its cached result. Concurrent requests can both invoke the provider before a result is saved.

## Cross-document contradictions

Configure the engine and provider as described above. In Swagger at
`http://127.0.0.1:8765/docs`, upload two to five PDFs through
`POST /api/documents/upload-multiple`. Copy their document UUIDs into
`POST /api/ai/contradictions`. Use the returned `analysis_id` with the two
GET endpoints to retrieve the analysis and its evidence.

```json
{
  "document_ids": [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002"
  ]
}
```

| Endpoint | Result |
| --- | --- |
| `POST /api/ai/contradictions` | Compares 2–5 unique document UUIDs. Returns the analysis UUID, requested IDs, timestamps, status, analyzed-document count, skipped OCR document IDs, findings, and warnings. |
| `GET /api/ai/contradictions/{analysis_id}` | Returns the saved analysis with its verified findings. |
| `GET /api/ai/contradictions/{analysis_id}/evidence` | Returns each finding ID alongside its two page citations. |

A `completed` analysis can have an empty findings list. Documents without
readable text are skipped and listed in `ocr_required_document_ids`. If fewer
than two readable documents remain, the endpoint saves an
`insufficient_readable_documents` result without calling the provider. Blank
pages are skipped while the original PDF page numbers are preserved.

The backend extracts and independently verifies claims separately for each
document, then calls the teammate's
`ContradictionDetector.detect(claims, verifications, original_evidence)`.
Each verified finding has two claim sides, an engine classification and
explanation, and exact stored quotations with document UUID, filename,
1-based page number, and evidence ID. Quotations must be verbatim substrings
of the stored page text. Findings missing either independent citation, or
flagged for human review, are excluded with a warning. Engine-labeled temporal
or version changes are excluded. The request carries no document version
labels, so the engine cannot infer undeclared versions. Identical filenames
with identical passages on the same page can cause the detector to abstain
because its verification quotes lack document IDs. The engine provides no
calibrated confidence or severity, so `confidence` is always `null`.

SQLite stores each analysis and its findings as a single snapshot, tied to
the submitted documents and their content fingerprints. Startup adds the
tables to existing databases. Deleting a source through the document API
removes its analyses and cached inputs in the same transaction. Changes to
stored text make earlier results unavailable, and changes during inference
prevent the new snapshot from being saved. When `SOUL_BACKEND_AI_CACHE_REVISION`
is set, comparisons reuse fully verified structured claim inputs by document
content, provider/model identity, and revision. This cache is separate from
the single-document response cache because the detector needs the original
claim fields and verification reports. Analyses themselves are saved as new
snapshots on each request.

The asynchronous processing budget is 120 seconds for extraction,
verification, and detection. The provider's own HTTP timeout still applies.
On timeout the request returns without saving an analysis. An in-flight
provider HTTP call may finish in its worker thread. Invalid IDs, duplicates,
and out-of-range document counts return HTTP 422. Missing documents and
unknown or stale analyses return 404. Source changes detected before saving
return 409. Missing provider configuration or a provider request failure
returns 503, a timeout returns 504, and unexpected errors return a generic
500. Backend tests use real engine components with controlled provider
responses and temporary SQLite databases.

See the [integration handoff](INTEGRATION_HANDOFF.md) for grounded Q&A, the complete API contract, and the frontend workflow.
