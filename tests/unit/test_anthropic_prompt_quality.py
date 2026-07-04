from unittest.mock import MagicMock, patch

from anthropic.types import ToolUseBlock

from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline

_ANTI_HALLUCINATION_PHRASES = ("never guess", "infer", "fabricate")
_PARTIAL_DOCUMENT_PHRASES = ("partial", "truncat")


def _pipeline() -> AnthropicFieldExtractionPipeline:
    return AnthropicFieldExtractionPipeline(
        api_key="test-key", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=12_000
    )


def _tool_use_response(input_fields: dict[str, object]) -> MagicMock:
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [
        ToolUseBlock(
            id="toolu_01", input=input_fields, name="record_clinical_fields", type="tool_use"
        )
    ]
    return response


def test_system_prompt_is_sent_and_warns_against_hallucination() -> None:
    pipeline = _pipeline()
    with patch.object(
        pipeline._client.messages, "create", return_value=_tool_use_response({})
    ) as mock_create:
        pipeline.extract_fields(raw_text="a clinical note")

    system_prompt = mock_create.call_args.kwargs["system"].lower()
    for phrase in _ANTI_HALLUCINATION_PHRASES:
        assert phrase in system_prompt


def test_system_prompt_addresses_partial_or_truncated_documents() -> None:
    pipeline = _pipeline()
    with patch.object(
        pipeline._client.messages, "create", return_value=_tool_use_response({})
    ) as mock_create:
        pipeline.extract_fields(raw_text="a clinical note")

    system_prompt = mock_create.call_args.kwargs["system"].lower()
    assert any(phrase in system_prompt for phrase in _PARTIAL_DOCUMENT_PHRASES)


def test_user_message_also_warns_about_truncation() -> None:
    pipeline = _pipeline()
    with patch.object(
        pipeline._client.messages, "create", return_value=_tool_use_response({})
    ) as mock_create:
        pipeline.extract_fields(raw_text="a clinical note")

    user_content = mock_create.call_args.kwargs["messages"][0]["content"].lower()
    assert "truncat" in user_content


def test_tool_choice_still_forces_the_extraction_tool() -> None:
    # Prompt strengthening must not loosen the schema-guided contract —
    # still a forced tool call, not free text.
    pipeline = _pipeline()
    with patch.object(
        pipeline._client.messages, "create", return_value=_tool_use_response({})
    ) as mock_create:
        pipeline.extract_fields(raw_text="a clinical note")

    assert mock_create.call_args.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "record_clinical_fields",
    }


def test_extraction_behavior_is_unchanged_by_the_prompt_update() -> None:
    # Regression: the stronger prompt must not change what a well-formed
    # tool response produces.
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
