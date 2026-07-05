from pydantic import BaseModel


class EvalCase(BaseModel):
    case_id: str
    raw_text: str
    expected_fields: dict[str, str] = {}
    expected_phi_labels: list[str] = []
    notes: str = ""


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
