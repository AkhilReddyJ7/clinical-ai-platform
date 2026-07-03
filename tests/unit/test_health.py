from fastapi.testclient import TestClient


def test_health_returns_healthy(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "healthy"
    assert body["database"] == "connected"
    assert "service" in body
    assert "version" in body
    assert "environment" in body


def test_health_returns_unhealthy_when_database_is_unreachable(
    unhealthy_db_client: TestClient,
) -> None:
    response = unhealthy_db_client.get("/health")
    assert response.status_code == 503

    body = response.json()
    assert body["status"] == "unhealthy"
    assert body["database"] == "unreachable"


def test_root_returns_welcome_message(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "message" in response.json()
