from fastapi.testclient import TestClient

from apps.api.main import app


def test_metrics_endpoint_reflects_uploaded_and_processed_documents(client: TestClient) -> None:
    upload_response = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note", "text/plain")},
    )
    document_id = upload_response.json()["id"]
    client.post(f"/documents/{document_id}/process")

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {"jobs", "documents", "confidence"}
    assert set(body["jobs"]["by_status"].keys()) == {
        "queued",
        "running",
        "retrying",
        "completed",
        "failed",
        "cancelled",
    }
    assert set(body["documents"]["by_status"].keys()) == {
        "uploaded",
        "processing",
        "extracted",
        "validated",
        "failed",
    }
    assert sum(body["jobs"]["by_status"].values()) >= 1
    assert sum(body["documents"]["by_status"].values()) >= 1


def test_metrics_endpoint_reports_null_confidence_stats_with_no_extractions(
    client: TestClient,
) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    confidence = response.json()["confidence"]
    assert confidence == {"count": 0, "min": None, "avg": None, "max": None}


def test_metrics_endpoint_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.get("/metrics")
    assert response.status_code == 401
