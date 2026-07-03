from fastapi.testclient import TestClient

from apps.api.dependencies import get_extraction_pipeline
from apps.api.main import app
from modules.ocr.tesseract import TesseractExtractionPipeline


def test_text_plain_upload_flows_real_text_into_phi_detection(client: TestClient) -> None:
    # Overrides just the extraction pipeline for this test — every other
    # test keeps using the fast MockExtractionPipeline via the `client`
    # fixture. text/plain needs no tesseract binary (pure passthrough), so
    # this runs in the same fast CI job as everything else, while still
    # proving real content reaches PHI detection end-to-end (see
    # docs/adr/0008, which noted this was previously untestable since the
    # mock never echoed real input).
    app.dependency_overrides[get_extraction_pipeline] = lambda: TesseractExtractionPipeline()

    upload = client.post(
        "/documents",
        files={
            "file": (
                "note.txt",
                b"test-fixture ssn 123-45-6789 (not a real person)",
                "text/plain",
            )
        },
    )
    document_id = upload.json()["id"]

    response = client.post(f"/documents/{document_id}/process")
    body = response.json()

    assert body["extraction"]["raw_text"] == "test-fixture ssn 123-45-6789 (not a real person)"
    assert body["validation"]["is_valid"] is False
    assert any("phi" in issue for issue in body["validation"]["issues"])


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
