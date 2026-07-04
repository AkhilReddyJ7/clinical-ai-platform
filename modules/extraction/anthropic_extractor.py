import anthropic
from anthropic.types import ToolUseBlock

from modules.extraction.base import (
    FieldExtractionError,
    FieldExtractionOutput,
    FieldExtractionPipeline,
)

_ALLOWED_FIELD_NAMES = ("patient_name", "date_of_birth", "mrn")

_EXTRACTION_TOOL_NAME = "record_clinical_fields"

_EXTRACTION_TOOL: anthropic.types.ToolParam = {
    "name": _EXTRACTION_TOOL_NAME,
    "description": (
        "Record structured clinical fields found in the document text. Omit "
        "any field that is not present in the text — never guess or "
        "fabricate a value."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "patient_name": {
                "type": "string",
                "description": "Patient's full name, exactly as it appears in the text.",
            },
            "date_of_birth": {
                "type": "string",
                "description": ("Patient's date of birth, in the format it appears in the text."),
            },
            "mrn": {
                "type": "string",
                "description": ("Medical record number (MRN), exactly as it appears in the text."),
            },
        },
        "additionalProperties": False,
    },
}


class AnthropicFieldExtractionPipeline(FieldExtractionPipeline):
    """Structured field extraction via the Anthropic API, using a forced
    tool call so the model's output is reliably shaped JSON rather than
    free text that needs parsing.

    Single-provider by design — see docs/adr/0019. FieldExtractionPipeline
    (modules/extraction/base.py) is the seam for a future second
    implementation, not a provider tree built in advance.
    """

    def __init__(
        self, *, api_key: str, model: str, timeout_seconds: float, max_input_chars: int
    ) -> None:
        # Deliberately does not raise here even when api_key is empty:
        # this pipeline is constructed once as a FastAPI dependency
        # (apps/api/dependencies.py), and a missing key should fail the one
        # request that needs it — the same graceful failed-document path as
        # any other extraction failure (docs/adr/0012) — not crash
        # dependency resolution with an unhandled 500 on every request,
        # including ones that would never have reached the LLM call anyway
        # (e.g. PHI-flagged documents).
        self._api_key = api_key
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
        self._model = model
        self._max_input_chars = max_input_chars

    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        text = raw_text.strip()
        if not text:
            return FieldExtractionOutput(fields={}, confidence=0.0)

        if not self._api_key:
            # Fail closed: never let a request fall through to calling the
            # API with no key configured.
            raise FieldExtractionError("Anthropic API key is not configured")

        # Bounds per-document LLM cost the same way max_pdf_pages bounds
        # per-document OCR cost (docs/adr/0016) — an unbounded raw_text
        # length is an unbounded per-request cost against a paid API.
        truncated = text[: self._max_input_chars]

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                tools=[_EXTRACTION_TOOL],
                tool_choice={"type": "tool", "name": _EXTRACTION_TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Extract the patient's name, date of birth, and medical "
                            "record number (MRN) from the following clinical document "
                            "text. Call the tool with only the fields you can find; "
                            "omit anything not present in the text.\n\n"
                            f"---\n{truncated}\n---"
                        ),
                    }
                ],
            )
        except anthropic.RateLimitError as exc:
            raise FieldExtractionError(f"Anthropic API rate limited: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise FieldExtractionError(
                f"Anthropic API error ({exc.status_code}): {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise FieldExtractionError(f"could not reach Anthropic API: {exc}") from exc

        if response.stop_reason != "tool_use":
            raise FieldExtractionError(
                f"Anthropic API did not return a tool call (stop_reason={response.stop_reason})"
            )

        tool_use_block = next(
            (block for block in response.content if isinstance(block, ToolUseBlock)), None
        )
        if tool_use_block is None or not isinstance(tool_use_block.input, dict):
            raise FieldExtractionError("Anthropic API returned no usable tool input")

        fields = {
            key: value.strip()
            for key, value in tool_use_block.input.items()
            if key in _ALLOWED_FIELD_NAMES and isinstance(value, str) and value.strip()
        }
        confidence = len(fields) / len(_ALLOWED_FIELD_NAMES) if fields else 0.0

        return FieldExtractionOutput(fields=fields, confidence=confidence)
