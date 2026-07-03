from fastapi.testclient import TestClient

from apps.api.main import app

client = TestClient(app)


def test_health_returns_healthy() -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "healthy"
    assert "service" in body
    assert "version" in body
    assert "environment" in body


def test_root_returns_welcome_message() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()
