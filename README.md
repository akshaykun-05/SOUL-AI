# SOUL AI integrated application

SOUL AI combines the existing static HTML frontend, FastAPI backend, and Python verification engine. The backend mounts the engine's existing integration service directly; there is no separate AI server.

## Install

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r SOUL_AI/requirements.txt -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
```

Set `SOUL_LLM_API_KEY` in `backend/.env` or in the process environment. The claim extraction and independent verification passes use separately constructed provider instances. The app starts without a key so health checks and docs remain available, while answer requests return a configuration error until a key is provided. Never put a key in frontend code or commit `.env` files.

## Start the application

The FastAPI server hosts both the API and the built React frontend in `dist/`. Run from the repository root:

```powershell
python -m uvicorn backend.app.main:app --reload
```

Open the frontend at `http://127.0.0.1:8000/`. The API health endpoint is `http://127.0.0.1:8000/api/health`, and Swagger UI is at `http://127.0.0.1:8000/docs`.

The frontend's `/assets/...` URLs and React routes (for example `/overview`, `/documents`, `/upload`, `/verification`) are served by FastAPI. Direct navigation and refreshes fall back to `dist/index.html`. `dist/api-config.js` is the single API base URL setting for the deployed build; `dist/soul-ai-bridge.js` adds `/api` and the backend paths. Set the Render service origin there before deploying the frontend. The older `frontend/soul-api.js` client consumes the same `window.SOUL_API_BASE_URL` setting.

The previous static HTML screens remain available separately at `frontend/page1.html` through `frontend/page11.html`. To run that legacy set with its standalone dev server, use a second terminal:

```powershell
python frontend/serve.py
```

Open `http://127.0.0.1:3000/page1.html`. The legacy server serves the same `dist/api-config.js` setting at `/config.js`, so both frontend versions use the configured Render origin.

The browser never receives the model API key. Backend CORS always allows `https://soul-ai-dist.vercel.app` in addition to configured development origins.

## Request flow

1. The upload page sends selected PDFs as base64 JSON to `POST /api/soul-ai/documents/extract`.
2. `SOULAIEngineService` calls the existing `PdfIngestor`, indexes page evidence in backend memory, and keeps the original PDFs available for citations.
3. The results page sends a question and the uploaded document IDs to `POST /api/soul-ai/answer`.
4. The existing lexical evidence index, claim extractor, independent verifier, contradiction detector, and zero-guessing response engine produce the response. The UI displays verified claims, both cited sides of conflicts, or an insufficient-evidence response.
5. A citation opens `GET /api/soul-ai/documents/{document_id}/file` at the cited PDF page.

The in-memory evidence and original PDFs last only while the backend process runs. This integration is intended for local/hackathon use; restarting the backend clears its indexed session. Upload limits default to 25 MiB per PDF and 10 PDFs per request. OCR remains opt-in and requires the optional Python OCR dependencies plus the Tesseract executable.

## Test

Run the existing engine and backend tests from the repository root:

```powershell
Push-Location SOUL_AI
python -m pytest
Pop-Location
Push-Location backend
python -m pytest
Pop-Location
```

The integration tests create real synthetic PDFs in memory and cover supported/conflicting source evidence, an absent fact, invalid PDF handling, and opening the original citation PDF. They do not measure the quality of a live external model. For a reproducible, model-free three-document conflict run, see `SOUL_AI/demos/phase7_three_documents.py`.

## Benchmark

The existing benchmark harness is available from the engine directory:

```powershell
Set-Location SOUL_AI
python -m benchmark.run --validate-only
```

Model-backed benchmark runs use the documented engine factory interface in `SOUL_AI/README.md` and are not exposed as an unauthenticated web endpoint.
