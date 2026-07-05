from typing import Literal

from pydantic import BaseModel


class EvalCase(BaseModel):
    case_id: str
    raw_text: str
    expected_fields: dict[str, str] = {}
    expected_phi_labels: list[str] = []
    notes: str = ""
    # ADR-0036. Default "baseline" keeps the 15 original cases valid
    # as-is with no JSONL edits. scripts/run_eval.py splits by this
    # field and reports each group separately -- no change needed to
    # scoring.py's aggregation functions, which stay category-agnostic.
    case_type: Literal["baseline", "adversarial"] = "baseline"


class FieldMetrics(BaseModel):
    field_name: str  # one of the extracted field names, or "overall"
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


class PHIMetrics(BaseModel):
    true_positives: int
    false_positives: int
    false_negatives: int
    true_negatives: int
    precision: float
    recall: float
    f1: float


class CaseResult(BaseModel):
    case_id: str
    predicted_fields: dict[str, str]
    expected_fields: dict[str, str]
    document_exact_match: bool
    expected_phi_flagged: bool
    predicted_phi_flagged: bool
    phi_issues: list[str]


class EvalReport(BaseModel):
    pipeline_name: str
    dataset_path: str
    case_count: int
    field_metrics: list[FieldMetrics]
    document_exact_match_rate: float
    phi_metrics: PHIMetrics
    cases: list[CaseResult]
