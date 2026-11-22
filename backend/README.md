# SOUL AI Backend

Use Python 3.10 or later. From the repository root in PowerShell:

```powershell
Set-Location .\backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open [Swagger UI](http://127.0.0.1:8000/docs) or check [service health](http://127.0.0.1:8000/api/health).

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
