import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.routers import documents as documents_router
from modules.audit.models import AuditAction, AuditLogEntry

# Matches conftest.py's `client` fixture, which authenticates every
# request as this caller label (TEST_API_KEY_LABEL).
_TEST_CALLER = "test-caller"


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


def test_upload_rejects_file_exceeding_max_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Lowers the limit rather than uploading a genuinely huge file — keeps
    # the test fast while still exercising the real chunked-read/reject
    # path (apps/api/routers/documents.py::_read_upload_within_limit), not
    # just a size arithmetic check.
    monkeypatch.setattr(documents_router.settings, "max_upload_size_bytes", 10)

    response = client.post(
        "/documents",
        files={"file": ("note.txt", b"this is definitely more than ten bytes", "text/plain")},
    )

    assert response.status_code == 413


def test_upload_accepts_file_exactly_at_max_size(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(documents_router.settings, "max_upload_size_bytes", 10)

    response = client.post(
        "/documents",
        files={"file": ("note.txt", b"0123456789", "text/plain")},  # exactly 10 bytes
    )

    assert response.status_code == 201


def test_get_unknown_document_returns_404(client: TestClient) -> None:
    response = client.get(f"/documents/{uuid.uuid4()}")
    assert response.status_code == 404


def test_process_document_enqueues_a_job(client: TestClient) -> None:
    """ADR-0022: POST /process no longer runs the pipeline inline -- it
    enqueues a job and returns 202 immediately."""
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    body = process_response.json()
    assert body["document_id"] == document_id
    assert body["job_status"] == "queued"
    assert uuid.UUID(body["job_id"])


@pytest.mark.asyncio
async def test_process_document_runs_extraction_and_validation_via_the_worker(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 200
    body = result_response.json()
    assert body["document"]["id"] == document_id
    assert body["document"]["status"] in {"validated", "failed"}
    assert body["extraction"]["fields"]["mrn"].startswith("MOCK-")
    assert isinstance(body["validation"]["is_valid"], bool)


def test_process_unknown_document_returns_404(client: TestClient) -> None:
    response = client.post(f"/documents/{uuid.uuid4()}/process")
    assert response.status_code == 404


def test_process_document_already_processing_returns_409(client: TestClient) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = upload.json()["id"]

    first = client.post(f"/documents/{document_id}/process")
    assert first.status_code == 202

    second = client.post(f"/documents/{document_id}/process")
    assert second.status_code == 409
    assert "result" in second.json()["detail"]


def test_result_never_processed_returns_200_not_404(client: TestClient) -> None:
    """ADR-0022's behavior change: "never processed" is distinct from
    "not found" -- both used to collapse into a bare 404."""
    upload = client.post(
        "/documents",
        files={"file": ("note2.txt", b"another synthetic note", "text/plain")},
    )
    document_id = upload.json()["id"]

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 200
    body = result_response.json()
    assert body["document"]["status"] == "uploaded"
    assert body["job_status"] is None
    assert body["extraction"] is None
    assert body["validation"] is None


def test_result_unknown_document_returns_404(client: TestClient) -> None:
    response = client.get(f"/documents/{uuid.uuid4()}/result")
    assert response.status_code == 404


def test_result_while_processing_reports_active_job_status(client: TestClient) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = upload.json()["id"]
    client.post(f"/documents/{document_id}/process")

    result_response = client.get(f"/documents/{document_id}/result")
    assert result_response.status_code == 200
    body = result_response.json()
    assert body["document"]["status"] == "processing"
    assert body["job_status"] == "queued"
    assert body["extraction"] is None
    assert body["validation"] is None


@pytest.mark.asyncio
async def test_upload_records_an_audit_entry(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])

    async with session_factory() as session:
        entries = (
            (
                await session.execute(
                    select(AuditLogEntry).where(AuditLogEntry.document_id == document_id)
                )
            )
            .scalars()
            .all()
        )

    assert len(entries) == 1
    assert entries[0].caller == _TEST_CALLER
    assert entries[0].action == AuditAction.DOCUMENT_UPLOADED
    assert entries[0].job_id is None


@pytest.mark.asyncio
async def test_process_records_an_audit_entry_with_the_job_id(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])

    process_response = client.post(f"/documents/{document_id}/process")
    job_id = uuid.UUID(process_response.json()["job_id"])

    async with session_factory() as session:
        entries = (
            (
                await session.execute(
                    select(AuditLogEntry).where(
                        AuditLogEntry.document_id == document_id,
                        AuditLogEntry.action == AuditAction.JOB_ENQUEUED,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(entries) == 1
    assert entries[0].caller == _TEST_CALLER
    assert entries[0].job_id == job_id


@pytest.mark.asyncio
async def test_a_409_rejected_process_call_records_no_audit_entry(
    client: TestClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The audit trail records actions that happened, not attempts --
    enqueue_job raising IllegalTransitionError means no job was created,
    so process_document never reaches its record_action call."""
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"synthetic clinical note content", "text/plain")},
    )
    document_id = uuid.UUID(upload.json()["id"])
    client.post(f"/documents/{document_id}/process")

    second = client.post(f"/documents/{document_id}/process")
    assert second.status_code == 409

    async with session_factory() as session:
        entries = (
            (
                await session.execute(
                    select(AuditLogEntry).where(
                        AuditLogEntry.document_id == document_id,
                        AuditLogEntry.action == AuditAction.JOB_ENQUEUED,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert len(entries) == 1  # only the first, successful enqueue
