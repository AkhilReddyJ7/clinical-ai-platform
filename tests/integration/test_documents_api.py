import uuid


def test_upload_creates_document_in_registry(client):
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
    assert any(doc["id"] == body["id"] for doc in list_response.json())

    get_response = client.get(f"/documents/{body['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == body["id"]


def test_upload_rejects_unsupported_content_type(client):
    response = client.post(
        "/documents",
        files={"file": ("note.exe", b"binary", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_upload_rejects_empty_file(client):
    response = client.post(
        "/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400


def test_get_unknown_document_returns_404(client):
    response = client.get(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404


def test_process_document_runs_extraction_and_validation(client):
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


def test_process_unknown_document_returns_404(client):
    response = client.post(f"/documents/{uuid.uuid4()}/process")
    assert response.status_code == 404


def test_result_before_processing_returns_404(client):
    upload = client.post(
        "/documents",
        files={"file": ("note2.txt", b"another synthetic note", "text/plain")},
    )
    document_id = upload.json()["id"]

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 404
