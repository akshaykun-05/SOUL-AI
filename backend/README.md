# SOUL AI Backend

Use Python 3.10 or later. Run the commands below from the repository root with the existing backend virtual environment activated. In PowerShell, activate it with `.\backend\.venv\Scripts\Activate.ps1`. On macOS or Linux, run `source backend/.venv/bin/activate`.

## Installation

```sh
python -m pip install -r backend/requirements.txt
```

The app runs with defaults. To customize it, copy `backend/.env.example` to `backend/.env` and edit it. Set `SOUL_BACKEND_CORS_ORIGINS` to a JSON array of frontend origins, including the scheme and port. The defaults allow localhost and 127.0.0.1 on ports 3000 and 5173. Process environment variables take precedence over the file.

## Startup

```sh
python -m uvicorn backend.app.main:app --reload
```

Open [Swagger UI](http://127.0.0.1:8000/docs) or check [service health](http://127.0.0.1:8000/api/health).

## Tests

```sh
python -m pytest backend/tests
```
