from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest
from anthropic.types import ToolUseBlock

from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.base import FieldExtractionError

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _pipeline(max_input_chars: int = 12_000) -> AnthropicFieldExtractionPipeline:
    return AnthropicFieldExtractionPipeline(
        api_key="test-key",
        model="claude-haiku-4-5",
        timeout_seconds=5.0,
        max_input_chars=max_input_chars,
    )


def _tool_use_response(input_fields: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [
        ToolUseBlock(
            id="toolu_01",
            input=input_fields,
            name="record_clinical_fields",
            type="tool_use",
        )
    ]
    return response


def test_missing_api_key_fails_closed_per_call_not_at_construction() -> None:
    # Construction must not raise: this pipeline is built once as a FastAPI
    # dependency, and a missing key should only fail the request that
    # actually needs it (see the comment in anthropic_extractor.py).
    pipeline = AnthropicFieldExtractionPipeline(
        api_key="", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=100
    )
    with patch.object(pipeline._client.messages, "create") as mock_create:
        with pytest.raises(FieldExtractionError, match="API key is not configured"):
            pipeline.extract_fields(raw_text="some clinical note text")

    mock_create.assert_not_called()


def test_empty_raw_text_returns_empty_output_without_calling_api() -> None:
    pipeline = _pipeline()
    with patch.object(pipeline._client.messages, "create") as mock_create:
        output = pipeline.extract_fields(raw_text="   ")

    mock_create.assert_not_called()
    assert output.fields == {}
    assert output.confidence == 0.0


def test_successful_tool_call_parses_fields_and_computes_confidence() -> None:
    pipeline = _pipeline()
    response = _tool_use_response(
        {"patient_name": "Jane Doe", "date_of_birth": "1980-01-01", "mrn": "12345"}
    )
    with patch.object(pipeline._client.messages, "create", return_value=response):
        output = pipeline.extract_fields(raw_text="clinical note mentioning Jane Doe")

    assert output.fields == {
        "patient_name": "Jane Doe",
        "date_of_birth": "1980-01-01",
        "mrn": "12345",
    }
    assert output.confidence == 1.0


def test_partial_fields_yield_partial_confidence() -> None:
    pipeline = _pipeline()
    response = _tool_use_response({"patient_name": "Jane Doe"})
    with patch.object(pipeline._client.messages, "create", return_value=response):
        output = pipeline.extract_fields(raw_text="clinical note mentioning Jane Doe")

    assert output.fields == {"patient_name": "Jane Doe"}
    assert output.confidence == pytest.approx(1 / 3)


def test_disallowed_and_blank_fields_are_dropped() -> None:
    pipeline = _pipeline()
    response = _tool_use_response(
        {"patient_name": "  Jane Doe  ", "mrn": "   ", "unexpected_field": "value"}
    )
    with patch.object(pipeline._client.messages, "create", return_value=response):
        output = pipeline.extract_fields(raw_text="some text")

    # Whitespace-only "mrn" and the schema-foreign "unexpected_field" are
    # both dropped; "patient_name" is stripped of surrounding whitespace.
    assert output.fields == {"patient_name": "Jane Doe"}


def test_no_fields_found_yields_zero_confidence() -> None:
    pipeline = _pipeline()
    response = _tool_use_response({})
    with patch.object(pipeline._client.messages, "create", return_value=response):
        output = pipeline.extract_fields(raw_text="text with nothing extractable")

    assert output.fields == {}
    assert output.confidence == 0.0


def test_non_tool_use_stop_reason_raises_field_extraction_error() -> None:
    pipeline = _pipeline()
    response = MagicMock()
    response.stop_reason = "refusal"
    response.content = []
    with patch.object(pipeline._client.messages, "create", return_value=response):
        with pytest.raises(FieldExtractionError, match="refusal"):
            pipeline.extract_fields(raw_text="some text")


def test_missing_tool_use_block_raises_field_extraction_error() -> None:
    pipeline = _pipeline()
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = []  # malformed: claims tool_use but has no such block
    with patch.object(pipeline._client.messages, "create", return_value=response):
        with pytest.raises(FieldExtractionError):
            pipeline.extract_fields(raw_text="some text")


def test_rate_limit_error_is_translated_to_field_extraction_error() -> None:
    pipeline = _pipeline()
    exc = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
    )
    with patch.object(pipeline._client.messages, "create", side_effect=exc):
        with pytest.raises(FieldExtractionError, match="rate limited"):
            pipeline.extract_fields(raw_text="some text")


def test_api_status_error_is_translated_to_field_extraction_error() -> None:
    pipeline = _pipeline()
    exc = anthropic.BadRequestError(
        "bad request", response=httpx.Response(400, request=_REQUEST), body=None
    )
    with patch.object(pipeline._client.messages, "create", side_effect=exc):
        with pytest.raises(FieldExtractionError, match="400"):
            pipeline.extract_fields(raw_text="some text")


def test_connection_error_is_translated_to_field_extraction_error() -> None:
    pipeline = _pipeline()
    exc = anthropic.APIConnectionError(request=_REQUEST)
    with patch.object(pipeline._client.messages, "create", side_effect=exc):
        with pytest.raises(FieldExtractionError, match="could not reach"):
            pipeline.extract_fields(raw_text="some text")


def test_raw_text_is_truncated_to_max_input_chars_before_sending() -> None:
    pipeline = _pipeline(max_input_chars=10)
    response = _tool_use_response({})
    with patch.object(pipeline._client.messages, "create", return_value=response) as mock_create:
        pipeline.extract_fields(raw_text="0123456789" * 10)

    sent_content = mock_create.call_args.kwargs["messages"][0]["content"]
    assert "0123456789" in sent_content
    assert "0123456789" * 10 not in sent_content
