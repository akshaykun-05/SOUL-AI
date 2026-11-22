# SOUL AI Backend

Use Python 3.10 or later. This FastAPI app mounts the existing `SOUL_AI` engine integration in process. See the repository root `README.md` for complete frontend and backend startup instructions.

## Install

From the repository root:

```powershell
python -m pip install -r SOUL_AI/requirements.txt -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
```

Set `SOUL_LLM_API_KEY` in `backend/.env` to enable live question answering. Model credentials are read only by the backend. Backend environment variables can also be set in the process environment and take precedence over `.env`.

## Start

```powershell
python -m uvicorn backend.app.main:app --reload
```

Open [Swagger UI](http://127.0.0.1:8000/docs) or [service health](http://127.0.0.1:8000/api/health). AI routes are `/api/soul-ai/documents/extract`, `/api/soul-ai/answer`, and `/api/soul-ai/documents/{document_id}/file`.

## Tests

```powershell
python -m pytest backend/tests
```
