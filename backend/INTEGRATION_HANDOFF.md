# SOUL AI backend integration handoff

The backend development URL is `http://127.0.0.1:8765`. Swagger is at
`http://127.0.0.1:8765/docs` and the machine-readable contract is at
`http://127.0.0.1:8765/openapi.json`.

## Run locally

From the repository root in PowerShell:

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = (Resolve-Path ..\SOUL_AI).Path
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
```

An editable engine install in the backend virtual environment can replace the
`PYTHONPATH` step. Startup initializes SQLite tables. The
[backend README](README.md) describes migration from Phase 2 if needed.

### Environment

Process environment overrides values in the ignored `backend/.env`. The tracked
`.env.example` is a template.

| Variable | Requirement and default |
| --- | --- |
| `SOUL_LLM_API_KEY` | Required for the configured HTTP model provider. Supply your own nonblank key on the backend. |
| `SOUL_LLM_PROVIDER` | Optional. Defaults to `openai`; also supports `openai-compatible`. |
| `SOUL_LLM_MODEL` | Optional. Engine default is `gpt-4.1-mini`. Set to a model supported by your provider. |
| `SOUL_LLM_BASE_URL` | Optional. Defaults to `https://api.openai.com/v1`. Set for a compatible service. |
| `SOUL_LLM_TEMPERATURE` | Optional engine setting. The Q&A prompts request temperature 0. |
| `SOUL_BACKEND_CORS_ORIGINS` | Optional JSON array of exact frontend origins. Defaults to HTTP localhost and 127.0.0.1 on ports 3000 and 5173. |
| `SOUL_BACKEND_DATABASE_URL` | Optional. Defaults to `sqlite:///data/soul_ai.sqlite3`. |
| `SOUL_BACKEND_UPLOAD_DIR` | Optional. Defaults to `uploads` under `backend/`. |
| `SOUL_BACKEND_MAX_UPLOAD_SIZE_MB` | Optional. Defaults to 10 MiB per file. |
| `SOUL_BACKEND_MAX_FILES_PER_UPLOAD` | Optional. Defaults to 5, configurable from 1 to 5. |
| `SOUL_BACKEND_AI_CACHE_REVISION` | Optional. Enables reuse of verified claim results/inputs when nonempty. Bump when changing an engine/model deployment. |
| `SOUL_BACKEND_SERVICE_NAME`, `SOUL_BACKEND_VERSION` | Optional service metadata. Defaults to `SOUL AI Backend` and `0.1.0`. |

Core health and document management work without provider configuration. Use
the actual browser origin for CORS, including the scheme and port. The backend
permits GET, POST, and DELETE from configured origins. Keep the model key in the
backend environment. The local API has no authentication, and AI operations send
selected text and citation metadata to the configured provider.

## Endpoint reference

Paths below are relative to the development URL. IDs are UUID strings. Use upload
responses as the source of document IDs. Comparison results return a separate
`analysis_id`. API timestamps are serialized as ISO 8601.

| Method and path | Request | Success response |
| --- | --- | --- |
| `GET /api/health` | None | 200, `{service, version, status}`, where status is `healthy`. |
| `POST /api/documents/upload` | Multipart file named `file`. | 201, `DocumentResponse`. |
| `POST /api/documents/upload-multiple` | Repeat multipart field `files` for 1–5 PDFs, subject to the configured limit. | 200, `{results}` in input order. Each item is `{status: "success", filename, document}` or `{status: "error", filename, status_code, detail}`. |
| `GET /api/documents` | Optional query parameters `limit` (default 20, max 100), `offset` (default 0), `extraction_status`, and literal filename substring `search`. | 200, `{items, total, limit, offset}`. Items are summaries sorted newest first. |
| `GET /api/documents/{document_id}` | Document UUID. | 200, `DocumentResponse`. |
| `GET /api/documents/{document_id}/file` | Document UUID. | 200, original binary PDF with `Content-Type: application/pdf` and attachment disposition. |
| `DELETE /api/documents/{document_id}` | Document UUID. | 204, empty body. Dependent contradiction analyses and cached inputs are removed. |
| `GET /api/ai/status` | None. | 200, `{status, capabilities, package_ready, dependencies_ready, provider_configured, api_key_present, diagnostics}`. Status is `available` or `unavailable`. The key field reports presence only and can be null. |
| `POST /api/ai/process/{document_id}` | Document UUID; no body. | 200, `{document_id, processing_status, engine_status, page_count, claims, evidence_references, unavailable_capabilities, warnings}`. |
| `POST /api/ai/contradictions` | JSON `{document_ids}` with 2–5 unique document UUIDs. | 200, `ComparisonResult`. |
| `GET /api/ai/contradictions/{analysis_id}` | Analysis UUID. | 200, persisted `ComparisonResult`. |
| `GET /api/ai/contradictions/{analysis_id}/evidence` | Analysis UUID. | 200, `{analysis_id, evidence}`. Each item is `{finding_id, source_a, source_b}`. |
| `POST /api/ai/ask` | JSON `{question, document_ids}`. Question is 1–1,000 characters after trimming; supply 1–5 unique document UUIDs. | 200, `{status, question, answer, citations, refusal_reason, warnings}`. |

### Response shapes and errors

- `DocumentResponse` contains `document_id`, `filename`, `page_count`, `total_characters`, `extraction_status` (`extracted` or `ocr_required`), `ocr_required`, and ordered `pages`. Each page has `page_number`, `text`, `character_count`, and `has_text`. List summaries omit `pages` and include `file_size_bytes`, `mime_type`, `created_at`, and `updated_at`.
- Single-document AI `processing_status` is `completed`, `insufficient_evidence`, or `ocr_required`. Claims carry `claim_id`, `claim_text`, and `evidence_references`. Evidence references for Phase 4 and 5 use `{evidence_id, document_id, filename, page_number, text}`.
- `ComparisonResult` contains `analysis_id`, `status` (`completed` or `insufficient_readable_documents`), `document_ids`, `documents_analyzed`, `ocr_required_document_ids`, `contradiction_count`, `contradictions`, `warnings`, `created_at`, and `completed_at`. Findings contain `id`, `claim_a`, `claim_b`, `contradiction_type`, `explanation`, `confidence` (null), and `verification_status` (`verified`). Each side is `{claim_id, text, value, evidence}`.
- A Q&A answer has `status: "answered"`, answer text, citations, and `refusal_reason: null`. Q&A citations are `{document_id, filename, page_number, quote}`. A refusal has `status: "refused"`, `answer: null`, and empty citations. Supported refusal reasons are `insufficient_evidence`, `ocr_required`, `conflicting_evidence`, and `evidence_limit`. No Q&A result is persisted.
- HTTP 400 indicates malformed multipart input or too many file parts. HTTP 413 indicates a size limit. HTTP 415 indicates an unsupported upload or request media type. HTTP 422 covers invalid request shapes, malformed IDs, and unprocessable files. HTTP 404 indicates missing documents, original files, or missing/stale analyses. HTTP 409 indicates that source content changed before an AI result could be returned or committed. HTTP 503 indicates missing engine/provider setup or a model request failure, such as authentication or quota. HTTP 504 indicates a comparison or Q&A time budget expired. HTTP 500 responses have safe generic details.
- Application errors use `{detail: string}`. FastAPI request-validation errors normally use `{detail: [...]}`. For a batch upload, HTTP 200 can contain file-level errors, so check each item's `status`.

## Frontend workflow for the team lead

1. Configure the frontend's backend base URL and add its development origin to CORS. Use the browser's `FormData` and let it set the multipart boundary. Save each successful upload's `document_id`.
2. Check `/api/ai/status` and display diagnostics for unavailable configuration. Provider readiness does not test remote credentials or quota.
3. Process an individual document with `POST /api/ai/process/{document_id}`. For differences between documents, send their UUIDs to `POST /api/ai/contradictions` and retain the resulting `analysis_id`.
4. Ask a scoped question with `POST /api/ai/ask`. Treat an HTTP 200 refusal as a valid UI state. Display the relevant refusal message and any warnings. Keep HTTP errors separate from refusals.
5. Render claim text, excerpts, filenames, and warnings as plain text. Cite the filename and 1-based PDF page. Use `document_id` to retrieve original bytes from `/api/documents/{document_id}/file` and navigate to the cited page in the viewer. Filenames may repeat and are display labels.

## Swagger acceptance steps

1. Upload one or more readable PDFs using `/api/documents/upload` or `/api/documents/upload-multiple`. Verify HTTP 201 or per-file success, nonempty text, and original page numbers.
2. Check `/api/ai/status`, then submit a document UUID to the single-document processing endpoint. Check the original file download.
3. Compare two PDFs with different values for the same entity and attribute. Open the saved analysis and evidence endpoints with its `analysis_id`.
4. Submit `{"question":"What is the approved project budget?","document_ids":["<replace with uploaded UUID>"]}` to `/api/ai/ask`. Match the quotation and page against the PDF. Ask an unrelated question and expect a refusal. A direct question with unresolved conflicting sources should refuse, while an explicit comparison can cite both independently supported sides.

## Q&A limits

The backend uses the teammate's lexical `InMemoryEvidenceIndex` and
`ZeroGuessingEngine.answer`. It bounds retrieval at 20 matching page passages
and 40,000 characters. If either budget is exceeded, the API refuses instead
of dropping matching evidence silently. OCR-only selections refuse without a
provider call. In mixed selections, unreadable documents are skipped and
identified in warnings. Identical quotes on the same page of selected documents
with the same filename may be ambiguous and lead to refusal.

The engine's extraction and independent verification are followed by exact
SQLite citation checks and a conservative model-based check that all parts of
the question are covered. Lexical retrieval can miss synonyms, and model-based
verification can abstain or misjudge evidence. The processing budget is 120
seconds for retrieval and inference. A provider HTTP call may finish in its
worker thread after the caller receives a timeout. Automated tests use a
controlled provider; live provider credentials and deployment are team-lead
verification tasks.
