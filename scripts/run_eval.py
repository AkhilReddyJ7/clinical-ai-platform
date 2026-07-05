"""CLI entrypoint for the evaluation harness (ADR-0030).

Usage:
    uv run python -m scripts.run_eval
    uv run python -m scripts.run_eval --live
    uv run python -m scripts.run_eval --report-out eval/reports/run.json --fail-under 0.8
"""

import argparse
import sys
from pathlib import Path

from modules.evaluation.dataset import load_cases
from modules.evaluation.report import render_json, render_text
from modules.evaluation.service import build_report
from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.base import FieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.validation.phi import PHIDetectionValidator
from shared.config.settings import get_settings

DEFAULT_DATASET = Path("eval/dataset/cases.jsonl")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--live",
        action="store_true",
        help="use the real AnthropicFieldExtractionPipeline instead of the mock (costs real API calls)",
    )
    parser.add_argument("--report-out", type=Path, default=None, help="write a JSON report here")
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="exit 1 if the document-level exact-match rate falls below this value",
    )
    args = parser.parse_args(argv)

    settings = get_settings()

    extractor: FieldExtractionPipeline
    if args.live:
        if not settings.anthropic_api_key:
            print("--live requires ANTHROPIC_API_KEY to be set", file=sys.stderr)
            return 2
        extractor = AnthropicFieldExtractionPipeline(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout_seconds=settings.anthropic_timeout_seconds,
            max_input_chars=settings.anthropic_max_input_chars,
        )
        pipeline_name = f"anthropic:{settings.anthropic_model}"
    else:
        extractor = MockFieldExtractionPipeline()
        pipeline_name = "mock"

    cases = load_cases(args.dataset)
    baseline_cases = [c for c in cases if c.case_type == "baseline"]
    adversarial_cases = [c for c in cases if c.case_type == "adversarial"]

    report = build_report(
        pipeline_name=pipeline_name,
        dataset_path=args.dataset,
        cases=baseline_cases,
        extractor=extractor,
        phi_validator=PHIDetectionValidator(),
    )
    print(render_text(report))

    # Adversarial cases (ADR-0036) are reported separately and are
    # informational only -- --fail-under gates the baseline report above,
    # never this one.
    if adversarial_cases:
        adversarial_report = build_report(
            pipeline_name=pipeline_name,
            dataset_path=args.dataset,
            cases=adversarial_cases,
            extractor=extractor,
            phi_validator=PHIDetectionValidator(),
        )
        print("\n=== Adversarial cases (informational, not gated by --fail-under) ===\n")
        print(render_text(adversarial_report))

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(render_json(report), encoding="utf-8")
        print(f"\nreport written to {args.report_out}")

    if args.fail_under is not None and report.document_exact_match_rate < args.fail_under:
        print(
            f"\ndocument exact-match rate {report.document_exact_match_rate:.2f} "
            f"is below --fail-under {args.fail_under:.2f}",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
