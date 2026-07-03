import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from shared.config.settings import get_settings


def test_documents_endpoint_accepts_correct_api_key(client: TestClient) -> None:
    response = client.get("/documents")
    assert response.status_code == 200


def test_documents_endpoint_rejects_missing_api_key(client: TestClient) -> None:
    # A fresh client sharing the same app (and its dependency overrides for
    # db/storage/etc.) but without the `client` fixture's default header.
    no_key_client = TestClient(app)
    response = no_key_client.get("/documents")
    assert response.status_code == 401


def test_documents_endpoint_rejects_wrong_api_key(client: TestClient) -> None:
    response = client.get("/documents", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_documents_endpoint_fails_closed_when_no_keys_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Settings mutation, not a dependency override — see the `client`
    # fixture's comment on why (ApiKeyGateMiddleware bypasses
    # dependency_overrides by design).
    monkeypatch.setattr(get_settings(), "api_keys", "")
    response = client.get("/documents")
    assert response.status_code == 503


def test_health_and_root_do_not_require_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    assert no_key_client.get("/health").status_code == 200
    assert no_key_client.get("/").status_code == 200
