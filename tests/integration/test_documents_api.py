import uuid

from fastapi.testclient import TestClient


def test_upload_creates_document_in_registry(client: TestClient) -> None:
    response = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note", "text/plain")},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["filename"] == "note.txt"
    assert body["status"] == "uploaded"

    list_response = client.get("/documents")
    assert list_response.status_code == 200
    listing = list_response.json()
    assert any(doc["id"] == body["id"] for doc in listing["items"])
    assert listing["total"] >= 1
    assert listing["limit"] == 20
    assert listing["offset"] == 0

    get_response = client.get(f"/documents/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


def test_list_documents_paginates_with_limit_and_offset(client: TestClient) -> None:
    uploaded_ids = []
    for i in range(5):
        response = client.post(
            "/documents",
            files={"file": (f"note-{i}.txt", f"synthetic note {i}".encode(), "text/plain")},
        )
        uploaded_ids.append(response.json()["id"])

    first_page = client.get("/documents", params={"limit": 2, "offset": 0})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["total"] == 5
    assert first_body["limit"] == 2
    assert first_body["offset"] == 0

    second_page = client.get("/documents", params={"limit": 2, "offset": 2})
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["items"]) == 2
    assert second_body["total"] == 5

    first_page_ids = {doc["id"] for doc in first_body["items"]}
    second_page_ids = {doc["id"] for doc in second_body["items"]}
    assert first_page_ids.isdisjoint(second_page_ids)

    # most recently uploaded document should be first
    assert first_body["items"][0]["id"] == uploaded_ids[-1]


def test_list_documents_rejects_out_of_range_limit(client: TestClient) -> None:
    too_large = client.get("/documents", params={"limit": 101})
    assert too_large.status_code == 422

    too_small = client.get("/documents", params={"limit": 0})
    assert too_small.status_code == 422

    negative_offset = client.get("/documents", params={"offset": -1})
    assert negative_offset.status_code == 422


def test_upload_rejects_unsupported_content_type(client: TestClient) -> None:
    response = client.post(
        "/documents",
        files={"file": ("note.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_upload_rejects_empty_file(client: TestClient) -> None:
    response = client.post(
        "/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400


def test_get_unknown_document_returns_404(client: TestClient) -> None:
    response = client.get(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404


def test_process_document_runs_extraction_and_validation(client: TestClient) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 200

    body = process_response.json()
    assert body["document"]["status"] in {"validated", "failed"}
    assert body["extraction"]["fields"]["document_type"] == "text/plain"
    assert isinstance(body["validation"]["is_valid"], bool)

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 200
    assert result_response.json()["document"]["id"] == document_id


def test_process_unknown_document_returns_404(client: TestClient) -> None:
    response = client.post(f"/documents/{uuid.uuid4()}/process")
    assert response.status_code == 404


def test_result_before_processing_returns_404(client: TestClient) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note2.txt", b"another synthetic note", "text/plain")},
    )
    document_id = upload.json()["id"]

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 404
