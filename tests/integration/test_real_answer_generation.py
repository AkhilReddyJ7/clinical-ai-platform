"""ADR-0038: one real Anthropic API round trip for grounded answering.
Structure-only assertions (answer exists, indices valid) -- remote LLM
behavior can drift, so content is not pinned; same posture as
test_eval_harness_live.py. Always skipped in CI (no key configured).
"""

import pytest

from modules.retrieval.anthropic_answer import AnthropicAnswerGenerator
from modules.retrieval.base import RetrievedChunk
from shared.config.settings import get_settings

pytestmark = pytest.mark.skipif(
    not get_settings().anthropic_api_key,
    reason="requires a real ANTHROPIC_API_KEY -- see docs/adr/0038-grounded-answer-endpoint.md",
)

_CHUNKS = [
    RetrievedChunk(
        document_id="11111111-1111-1111-1111-111111111111",
        extraction_id="22222222-2222-2222-2222-222222222222",
        chunk_index=0,
        chunk_text=(
            "Primary care follow-up. The patient's hypertension is treated "
            "with lisinopril 20 mg daily; home readings average 132/84."
        ),
        score=0.9,
    ),
    RetrievedChunk(
        document_id="33333333-3333-3333-3333-333333333333",
        extraction_id="44444444-4444-4444-4444-444444444444",
        chunk_index=0,
        chunk_text=(
            "Orthopedic note. Right knee arthroscopy performed without "
            "complication; physical therapy to begin in two weeks."
        ),
        score=0.7,
    ),
]


def test_real_grounded_answer_has_valid_structure() -> None:
    settings = get_settings()
    generator = AnthropicAnswerGenerator(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_context_chars=settings.answer_max_context_chars,
    )

    result = generator.generate(
        question="What medication treats the patient's high blood pressure?", chunks=_CHUNKS
    )

    assert result.answer.strip()
    assert isinstance(result.insufficient_context, bool)
    assert all(0 <= i < len(_CHUNKS) for i in result.cited_chunk_indices)
