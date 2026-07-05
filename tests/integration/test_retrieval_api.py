import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app


def test_query_retrieval_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.post("/retrieval/query", json={"query": "diabetes"})
    assert response.status_code == 401


def test_query_retrieval_with_no_indexed_documents_returns_empty(client: TestClient) -> None:
    response = client.post("/retrieval/query", json={"query": "anything at all"})
    assert response.status_code == 200
    body = response.json()
    assert body["query"] == "anything at all"
    assert body["results"] == []


def test_query_retrieval_rejects_top_k_above_the_configured_max(client: TestClient) -> None:
    response = client.post("/retrieval/query", json={"query": "diabetes", "top_k": 10_000})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_query_retrieval_returns_the_indexed_chunk_of_a_validated_document(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={
            "file": (
                "note.txt",
                b"synthetic clinical note content about diabetes management",
                "text/plain",
            )
        },
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")
    await process_job(document_id)

    result = client.get(f"/documents/{document_id}/result")
    assert result.json()["document"]["status"] == "validated"

    response = client.post("/retrieval/query", json={"query": "diabetes management", "top_k": 5})
    assert response.status_code == 200
    body = response.json()
    assert len(body["results"]) >= 1
    assert body["results"][0]["document_id"] == str(document_id)
