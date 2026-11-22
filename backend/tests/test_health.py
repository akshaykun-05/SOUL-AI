import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SOUL_BACKEND_SERVICE_NAME", "SOUL AI Test Backend")
    monkeypatch.setenv("SOUL_BACKEND_VERSION", "1.2.3")
    monkeypatch.setenv("SOUL_BACKEND_CORS_ORIGINS", '["http://localhost:5173"]')
    with TestClient(create_app(Settings(_env_file=None))) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "SOUL AI Test Backend",
        "version": "1.2.3",
        "status": "healthy",
    }


@pytest.mark.parametrize("origin, allowed", [
    ("http://localhost:5173", True),
    ("https://example.com", False),
])
def test_health_cors(client, origin, allowed):
    response = client.get("/api/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == (
        origin if allowed else None
    )


def test_swagger_docs(client):
    assert client.get("/docs").status_code == 200
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert "get" in response.json()["paths"]["/api/health"]
