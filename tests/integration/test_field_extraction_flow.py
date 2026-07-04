import uuid
from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from apps.api.dependencies import get_extraction_pipeline, get_field_extraction_pipeline
from apps.api.main import app
from modules.extraction.base import (
    FieldExtractionError,
    FieldExtractionOutput,
    FieldExtractionPipeline,
)
from modules.ocr.tesseract import TesseractExtractionPipeline


class _NeverCallMeFieldExtractionPipeline(FieldExtractionPipeline):
    """Fails the test if the LLM stage is ever invoked — used to prove PHI
    detection gates the call rather than merely being checked alongside it.
    """

    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        raise AssertionError(
            "field extraction pipeline must not be called when raw_text is PHI-flagged"
        )


class _PartialFieldExtractionPipeline(FieldExtractionPipeline):
    """Always returns an incomplete field set, to prove RequiredFieldsValidator
    can now genuinely fail against real (LLM) output — unlike the old
    synthetic fields, which always populated all three required fields.
    """

    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        return FieldExtractionOutput(fields={"patient_name": "Jane Doe"}, confidence=1 / 3)


class _FailingFieldExtractionPipeline(FieldExtractionPipeline):
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        raise FieldExtractionError("simulated provider outage")


@pytest.mark.asyncio
async def test_phi_detected_skips_field_extraction_call_entirely(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()
    app.dependency_overrides[get_field_extraction_pipeline] = (
        lambda: _NeverCallMeFieldExtractionPipeline()
    )

    sensitive_text = b"test-fixture ssn 123-45-6789 (not a real person)"
    upload = client.post(
        "/documents",
        files={"file": ("note.txt", sensitive_text, "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    response = client.get(f"/documents/{document_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["document"]["status"] == "failed"
    assert body["extraction"]["raw_text"].startswith("[REDACTED:")
    assert any("phi" in issue for issue in body["validation"]["issues"])


@pytest.mark.asyncio
async def test_incomplete_llm_fields_fail_required_fields_validation(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    app.dependency_overrides[get_field_extraction_pipeline] = (
        lambda: _PartialFieldExtractionPipeline()
    )

    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"clean clinical note, no PHI patterns here", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    response = client.get(f"/documents/{document_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["document"]["status"] == "failed"
    assert body["extraction"]["fields"] == {"patient_name": "Jane Doe"}
    assert body["validation"]["is_valid"] is False
    assert any("missing required field: mrn" in issue for issue in body["validation"]["issues"])
    assert any(
        "missing required field: date_of_birth" in issue for issue in body["validation"]["issues"]
    )


@pytest.mark.asyncio
async def test_field_extraction_error_fails_cleanly_instead_of_crashing(
    client: TestClient, process_job: Callable[[uuid.UUID], Awaitable[None]]
) -> None:
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()
    app.dependency_overrides[get_field_extraction_pipeline] = (
        lambda: _FailingFieldExtractionPipeline()
    )

    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"clean clinical note, no PHI patterns here", "text/plain")},
    )
    document_id = upload.json()["id"]

    process_response = client.post(f"/documents/{document_id}/process")
    assert process_response.status_code == 202

    await process_job(uuid.UUID(document_id))

    response = client.get(f"/documents/{document_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["document"]["status"] == "failed"
    # raw_text is real and PHI-clean, so it's still persisted as-is — only
    # the field-extraction stage failed, not the OCR stage.
    assert body["extraction"]["raw_text"] == "clean clinical note, no PHI patterns here"
    assert body["extraction"]["fields"] == {}
    assert body["validation"]["is_valid"] is False
    assert any("field extraction failed" in issue for issue in body["validation"]["issues"])
