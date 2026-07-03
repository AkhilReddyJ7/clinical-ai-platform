from fastapi.testclient import TestClient

from apps.api.dependencies import get_extraction_pipeline
from apps.api.main import app
from modules.ocr.tesseract import TesseractExtractionPipeline


def test_phi_detected_in_real_text_is_redacted_before_persisting(client: TestClient) -> None:
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

    response = client.post(f"/documents/{document_id}/process")
    body = response.json()

    assert sensitive_text.decode() not in response.text
    assert body["extraction"]["raw_text"].startswith("[REDACTED:")
    assert body["extraction"]["fields"] == {}
    assert body["validation"]["is_valid"] is False
    assert any("phi" in issue for issue in body["validation"]["issues"])
    assert body["document"]["status"] == "failed"

    # Confirm the redaction is what's actually persisted, not just what
    # this one response happens to show.
    result = client.get(f"/documents/{document_id}/result")
    assert sensitive_text.decode() not in result.text
    assert result.json()["extraction"]["raw_text"].startswith("[REDACTED:")


def test_text_plain_upload_without_phi_patterns_passes(client: TestClient) -> None:
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()

    upload = client.post(
        "/documents",
        files={"file": ("note.txt", b"patient seen for routine follow-up visit", "text/plain")},
    )
    document_id = upload.json()["id"]

    response = client.post(f"/documents/{document_id}/process")
    body = response.json()

    assert body["extraction"]["raw_text"] == "patient seen for routine follow-up visit"
    assert body["extraction"]["confidence"] == 1.0
    assert body["validation"]["is_valid"] is True
