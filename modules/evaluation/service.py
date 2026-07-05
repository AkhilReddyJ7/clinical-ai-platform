from pathlib import Path

from modules.evaluation.schemas import CaseResult, EvalCase, EvalReport
from modules.evaluation.scoring import (
    aggregate_field_metrics,
    aggregate_phi_metrics,
    score_fields,
    score_phi,
)
from modules.extraction.base import FieldExtractionPipeline
from modules.ocr.base import ExtractionOutput
from modules.validation.base import ValidationPipeline


def build_report(
    *,
    pipeline_name: str,
    dataset_path: Path,
    cases: list[EvalCase],
    extractor: FieldExtractionPipeline,
    phi_validator: ValidationPipeline,
) -> EvalReport:
    """Runs every case through the given extraction pipeline and PHI
    validator and scores both (ADR-0030). `phi_validator` receives a
    throwaway `ExtractionOutput(raw_text=..., fields={}, confidence=0.0)`
    -- the same construction `modules/processing/pipeline.py`'s
    PHI-precheck already uses to run text-only PHI detection.
    """
    field_outcomes_per_case = []
    phi_outcomes_per_case = []
    case_results = []

    for case in cases:
        extraction = extractor.extract_fields(raw_text=case.raw_text)
        field_outcomes = score_fields(case.expected_fields, extraction.fields)
        field_outcomes_per_case.append(field_outcomes)
        document_exact_match = all(fp == 0 and fn == 0 for _, fp, fn in field_outcomes.values())

        phi_result = phi_validator.validate(ExtractionOutput(raw_text=case.raw_text))
        expected_phi_flagged = bool(case.expected_phi_labels)
        predicted_phi_flagged = not phi_result.is_valid
        phi_outcomes_per_case.append(
            score_phi(
                expected_flagged=expected_phi_flagged, predicted_flagged=predicted_phi_flagged
            )
        )

        case_results.append(
            CaseResult(
                case_id=case.case_id,
                predicted_fields=extraction.fields,
                expected_fields=case.expected_fields,
                document_exact_match=document_exact_match,
                expected_phi_flagged=expected_phi_flagged,
                predicted_phi_flagged=predicted_phi_flagged,
                phi_issues=phi_result.issues,
            )
        )

    exact_match_rate = (
        sum(1 for r in case_results if r.document_exact_match) / len(case_results)
        if case_results
        else 0.0
    )

    return EvalReport(
        pipeline_name=pipeline_name,
        dataset_path=str(dataset_path),
        case_count=len(cases),
        field_metrics=aggregate_field_metrics(field_outcomes_per_case),
        document_exact_match_rate=exact_match_rate,
        phi_metrics=aggregate_phi_metrics(phi_outcomes_per_case),
        cases=case_results,
    )
