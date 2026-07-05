from fastapi.testclient import TestClient

from apps.api.main import app

# Matches conftest.py's `client` fixture, which authenticates every
# request as this caller label (TEST_API_KEY_LABEL).
_TEST_CALLER = "test-caller"


def test_upload_and_process_are_visible_through_the_audit_endpoint(client: TestClient) -> None:
    upload_response = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note", "text/plain")},
    )
    document_id = upload_response.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    job_id = process_response.json()["job_id"]

    audit_response = client.get("/audit", params={"document_id": document_id})
    assert audit_response.status_code == 200
    body = audit_response.json()

    actions = {entry["action"] for entry in body["items"]}
    assert actions == {"document_uploaded", "job_enqueued"}
    assert all(entry["document_id"] == document_id for entry in body["items"])
    assert all(entry["caller"] == _TEST_CALLER for entry in body["items"])
    # newest first: job_enqueued was recorded after document_uploaded
    assert body["items"][0]["job_id"] == job_id


def test_audit_entries_filter_by_action(client: TestClient) -> None:
    upload_response = client.post(
        "/documents",
        files={"file": ("note2.txt", b"synthetic clinical note 2", "text/plain")},
    )
    document_id = upload_response.json()["id"]

    response = client.get(
        "/audit", params={"document_id": document_id, "action": "document_uploaded"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "document_uploaded"


def test_audit_endpoint_paginates_with_limit_and_offset(client: TestClient) -> None:
    document_ids = []
    for i in range(3):
        response = client.post(
            "/documents",
            files={"file": (f"note-{i}.txt", f"synthetic note {i}".encode(), "text/plain")},
        )
        document_ids.append(response.json()["id"])

    first_page = client.get("/audit", params={"limit": 1, "offset": 0})
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["items"]) == 1
    assert first_body["limit"] == 1
    assert first_body["offset"] == 0

    second_page = client.get("/audit", params={"limit": 1, "offset": 1})
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert first_body["items"][0]["id"] != second_body["items"][0]["id"]


def test_audit_endpoint_requires_api_key(client: TestClient) -> None:
    no_key_client = TestClient(app)
    response = no_key_client.get("/audit")
    assert response.status_code == 401
