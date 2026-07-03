from modules.ocr.mock import MockExtractionPipeline


def test_mock_extraction_returns_synthetic_fields() -> None:
    pipeline = MockExtractionPipeline()
    output = pipeline.extract(data=b"sample clinical note content", content_type="text/plain")

    assert output.raw_text
    assert output.confidence > 0
    assert {"patient_name", "date_of_birth", "mrn", "document_type"} <= output.fields.keys()
    assert output.fields["document_type"] == "text/plain"


def test_mock_extraction_is_deterministic_per_input() -> None:
    pipeline = MockExtractionPipeline()
    first = pipeline.extract(data=b"same bytes", content_type="text/plain")
    second = pipeline.extract(data=b"same bytes", content_type="text/plain")
    assert first.fields == second.fields


def test_mock_extraction_handles_empty_input() -> None:
    pipeline = MockExtractionPipeline()
    output = pipeline.extract(data=b"", content_type="text/plain")

    assert output.fields == {}
    assert output.confidence == 0.0
