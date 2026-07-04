from modules.extraction.mock import MockFieldExtractionPipeline


def test_mock_field_extraction_returns_synthetic_fields() -> None:
    pipeline = MockFieldExtractionPipeline()
    output = pipeline.extract_fields(raw_text="sample OCR'd clinical note text")

    assert output.confidence > 0
    assert {"patient_name", "date_of_birth", "mrn"} <= output.fields.keys()


def test_mock_field_extraction_is_deterministic_per_input() -> None:
    pipeline = MockFieldExtractionPipeline()
    first = pipeline.extract_fields(raw_text="same OCR text")
    second = pipeline.extract_fields(raw_text="same OCR text")
    assert first.fields == second.fields


def test_mock_field_extraction_handles_empty_input() -> None:
    pipeline = MockFieldExtractionPipeline()
    output = pipeline.extract_fields(raw_text="   ")

    assert output.fields == {}
    assert output.confidence == 0.0
