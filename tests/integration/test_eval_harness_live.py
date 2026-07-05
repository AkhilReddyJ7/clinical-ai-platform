"""ADR-0030: the actual discharge of the live-Anthropic-credentials
verification deferred since ADR-0019 -- runs the evaluation harness
against the real AnthropicFieldExtractionPipeline and the full committed
dataset. Always skipped without a real ANTHROPIC_API_KEY (true in CI
today); the moment a real key exists locally, running this test *is*
that verification.
"""

from pathlib import Path

import pytest

from modules.evaluation.dataset import load_cases
from modules.evaluation.service import build_report
from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.validation.phi import PHIDetectionValidator
from shared.config.settings import get_settings

DATASET_PATH = Path("eval/dataset/cases.jsonl")


@pytest.mark.skipif(
    not get_settings().anthropic_api_key,
    reason="requires a real ANTHROPIC_API_KEY -- see docs/adr/0030-evaluation-harness.md",
)
def test_eval_harness_runs_against_the_real_anthropic_pipeline() -> None:
    settings = get_settings()
    extractor = AnthropicFieldExtractionPipeline(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        timeout_seconds=settings.anthropic_timeout_seconds,
        max_input_chars=settings.anthropic_max_input_chars,
    )
    cases = load_cases(DATASET_PATH)

    report = build_report(
        pipeline_name=f"anthropic:{settings.anthropic_model}",
        dataset_path=DATASET_PATH,
        cases=cases,
        extractor=extractor,
        phi_validator=PHIDetectionValidator(),
    )

    # Structural validity is the point of this test -- the report's actual
    # accuracy numbers are what the run is *for*, not a hardcoded pass
    # condition to assert against.
    assert report.case_count == len(cases)
    assert len(report.cases) == len(cases)
    assert 0.0 <= report.document_exact_match_rate <= 1.0
