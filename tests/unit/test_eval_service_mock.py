"""ADR-0030: build_report against MockFieldExtractionPipeline + the real
PHIDetectionValidator, using small inline cases (not the committed
dataset, so this test doesn't drift when the dataset is edited).
"""

from pathlib import Path

from modules.evaluation.schemas import EvalCase
from modules.evaluation.service import build_report
from modules.extraction.mock import MockFieldExtractionPipeline, synthesize_fields_from_text
from modules.validation.phi import PHIDetectionValidator


def test_build_report_scores_a_case_the_mock_gets_exactly_right() -> None:
    raw_text = "a note whose mock-synthesized fields we pin down first"
    expected_fields = synthesize_fields_from_text(raw_text)
    cases = [EvalCase(case_id="case-a", raw_text=raw_text, expected_fields=expected_fields)]

    report = build_report(
        pipeline_name="mock",
        dataset_path=Path("inline"),
        cases=cases,
        extractor=MockFieldExtractionPipeline(),
        phi_validator=PHIDetectionValidator(),
    )

    assert report.case_count == 1
    assert report.document_exact_match_rate == 1.0
    assert report.cases[0].document_exact_match is True
    overall = next(m for m in report.field_metrics if m.field_name == "overall")
    assert overall.false_positives == 0
    assert overall.false_negatives == 0


def test_build_report_scores_a_case_the_mock_gets_wrong() -> None:
    cases = [
        EvalCase(
            case_id="case-b",
            raw_text="unrelated note text",
            expected_fields={"mrn": "MRN-999999"},  # the mock will never produce this literal value
        )
    ]

    report = build_report(
        pipeline_name="mock",
        dataset_path=Path("inline"),
        cases=cases,
        extractor=MockFieldExtractionPipeline(),
        phi_validator=PHIDetectionValidator(),
    )

    assert report.document_exact_match_rate == 0.0
    assert report.cases[0].document_exact_match is False


def test_build_report_flags_phi_independently_of_field_correctness() -> None:
    cases = [
        EvalCase(
            case_id="case-c",
            raw_text="contact via 123-45-6789 for insurance purposes",
            expected_fields={},
            expected_phi_labels=["SSN-like number"],
        )
    ]

    report = build_report(
        pipeline_name="mock",
        dataset_path=Path("inline"),
        cases=cases,
        extractor=MockFieldExtractionPipeline(),
        phi_validator=PHIDetectionValidator(),
    )

    assert report.phi_metrics.true_positives == 1
    assert report.cases[0].expected_phi_flagged is True
    assert report.cases[0].predicted_phi_flagged is True
    assert any("SSN-like number" in issue for issue in report.cases[0].phi_issues)


def test_build_report_with_no_cases_is_well_formed() -> None:
    report = build_report(
        pipeline_name="mock",
        dataset_path=Path("inline"),
        cases=[],
        extractor=MockFieldExtractionPipeline(),
        phi_validator=PHIDetectionValidator(),
    )

    assert report.case_count == 0
    assert report.document_exact_match_rate == 0.0
    assert report.cases == []
