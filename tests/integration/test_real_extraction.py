import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies import get_extraction_pipeline
from apps.api.main import app
from modules.ocr.tesseract import TesseractExtractionPipeline


@pytest.mark.asyncio
async def test_phi_detected_in_real_text_is_redacted_before_persisting(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    # Overrides just the extraction pipeline for this test — every other
    # test keeps using the fast MockExtractionPipeline via the `client`
    # fixture. text/plain needs no tesseract binary (pure passthrough), so
    # this runs in the same fast CI job as everything else, while still
    # proving real content reaches PHI detection end-to-end (see
    # docs/adr/0008, which noted this was previously untestable since the
    # mock never echoed real input) — and, since docs/adr/0010, that a PHI
    # finding gates what gets written, not just flagged after the fact: the
    # fake-but-pattern-shaped SSN below must never appear in the stored
    # extraction or the API response.
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()

    sensitive_text = b"test-fixture ssn 123-45-6789 (not a real person)"
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", sensitive_text, "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202
    assert sensitive_text.decode() not in process_response.text

    await process_job(uuid.UUID(document_id))

    # Confirm the redaction is what's actually persisted, not just what
    # any one response happens to show.
    result = client.get(f"/documents/{document_id}/result")
    body = result.json()

    assert sensitive_text.decode() not in result.text
    assert body["extraction"]["raw_text"].startswith("[REDACTED:")
    assert body["extraction"]["fields"] == {}
    assert body["validation"]["is_valid"] is False
    assert any("phi" in issue for issue in body["validation"]["issues"])
    assert body["document"]["status"] == "failed"


@pytest.mark.asyncio
async def test_corrupted_image_fails_cleanly_instead_of_crashing(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    # Upload accepts any bytes for an allowed content type (it doesn't
    # decode the file), so a mismatched/corrupted upload only surfaces at
    # process time — exactly the case that used to leave the document
    # stuck in PROCESSING behind an unhandled 500.
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()

    upload = client.post(
        "/documents",
        files={"file": ("fake.png", b"this is not a real png file", "image/png")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    result = client.get(f"/documents/{document_id}/result")
    assert result.status_code == 200
    body = result.json()
    assert body["document"]["status"] == "failed"
    assert body["extraction"]["raw_text"].startswith("[EXTRACTION FAILED:")
    assert body["validation"]["is_valid"] is False
    assert any("extraction failed" in issue for issue in body["validation"]["issues"])

    # Not stuck in PROCESSING — a follow-up GET reflects the failure too.
    get_response = client.get(f"/documents/{document_id}")
    assert get_response.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_text_plain_upload_without_phi_patterns_passes(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()

    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"patient seen for routine follow-up visit", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    result = client.get(f"/documents/{document_id}/result")
    body = result.json()

    assert body["extraction"]["raw_text"] == "patient seen for routine follow-up visit"
    # Geometric mean (ADR-0025, modules/processing/pipeline.py's
    # _aggregate_confidence) of the OCR stage's confidence (1.0 for
    # text/plain, direct decode) and MockFieldExtractionPipeline's fixed
    # confidence (0.9): sqrt(1.0 * 0.9).
    assert body["extraction"]["confidence"] == pytest.approx(0.9486832980505138)
    assert body["validation"]["is_valid"] is True
