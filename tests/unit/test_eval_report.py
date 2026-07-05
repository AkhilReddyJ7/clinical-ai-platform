from modules.evaluation.report import render_json, render_text
from modules.evaluation.schemas import CaseResult, EvalReport, FieldMetrics, PHIMetrics


def _sample_report() -> EvalReport:
    return EvalReport(
        pipeline_name="mock",
        dataset_path="eval/dataset/cases.jsonl",
        case_count=1,
        field_metrics=[
            FieldMetrics(
                field_name="mrn",
                true_positives=1,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            ),
            FieldMetrics(
                field_name="overall",
                true_positives=1,
                false_positives=0,
                false_negatives=0,
                precision=1.0,
                recall=1.0,
                f1=1.0,
            ),
        ],
        document_exact_match_rate=1.0,
        phi_metrics=PHIMetrics(
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            true_negatives=1,
            precision=0.0,
            recall=0.0,
            f1=0.0,
        ),
        cases=[
            CaseResult(
                case_id="case-001",
                predicted_fields={"mrn": "MRN-1"},
                expected_fields={"mrn": "MRN-1"},
                document_exact_match=True,
                expected_phi_flagged=False,
                predicted_phi_flagged=False,
                phi_issues=[],
            )
        ],
    )


def test_render_text_includes_pipeline_name_and_case_id() -> None:
    text = render_text(_sample_report())
    assert "pipeline=mock" in text
    assert "case-001" in text
    assert "match" in text


def test_render_json_round_trips_through_the_schema() -> None:
    report = _sample_report()
    parsed = EvalReport.model_validate_json(render_json(report))
    assert parsed == report
