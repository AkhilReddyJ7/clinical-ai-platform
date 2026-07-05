import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.main import app
from modules.ocr.models import ExtractionResult


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
    assert confidence == {
        "count": 0,
        "min": None,
        "avg": None,
        "max": None,
        "low_confidence_count": 0,
    }


def test_metrics_endpoint_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.get("/metrics")
    assert response.status_code == 401


def test_low_confidence_documents_endpoint_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.get("/metrics/low-confidence-documents")
    assert response.status_code == 401


def test_low_confidence_documents_endpoint_empty_with_no_extractions(client: TestClient) -> None:
    response = client.get("/metrics/low-confidence-documents")
    assert response.status_code == 200
    body = response.json()
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_low_confidence_documents_endpoint_rejects_out_of_range_limit(client: TestClient) -> None:
    response = client.get("/metrics/low-confidence-documents", params={"limit": 101})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_low_confidence_documents_endpoint_lists_a_below_threshold_document(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])

    async with session_factory() as session:
        session.add(
            ExtractionResult(
                document_id=document_id, raw_text="low confidence note", confidence=0.1
            )
        )
        await session.commit()

    response = client.get("/metrics/low-confidence-documents")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["document_id"] == str(document_id)
    assert body["items"][0]["confidence"] == 0.1
