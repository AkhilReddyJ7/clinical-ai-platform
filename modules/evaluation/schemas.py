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


class RetrievalCorpusDoc(BaseModel):
    doc_id: str
    text: str
    notes: str = ""


class RetrievalQueryCase(BaseModel):
    query_id: str
    query_text: str
    # Empty list marks a no-answer query (ADR-0037): reported with its
    # top-1 score so a human can judge abstention feasibility, but never
    # part of any aggregate denominator -- the retrieval stack has no
    # score threshold to gate on (ADR-0035).
    relevant_doc_ids: list[str] = []
    notes: str = ""


class RetrievalMetrics(BaseModel):
    scored_query_count: int  # queries with >= 1 relevant doc
    mrr: float
    recall_at_1: float
    recall_at_5: float
    hit_rate_at_1: float
    hit_rate_at_5: float


class RetrievalQueryResult(BaseModel):
    query_id: str
    ranked_doc_ids: list[str]
    relevant_doc_ids: list[str]
    reciprocal_rank: float  # 0.0 when no relevant doc was retrieved at all
    recall_at_1: float
    recall_at_5: float
    hit_at_1: bool
    hit_at_5: bool
    top_score: float | None  # cosine score of the rank-1 chunk, None if nothing retrieved


class RetrievalEvalReport(BaseModel):
    pipeline_name: str
    corpus_path: str
    queries_path: str
    corpus_doc_count: int
    chunk_count: int
    query_count: int
    metrics: RetrievalMetrics
    queries: list[RetrievalQueryResult]
    # No-answer queries (relevant_doc_ids == []) live here, outside every
    # aggregate -- same reported-but-not-gated posture as ADR-0036's
    # adversarial cases.
    no_answer_queries: list[RetrievalQueryResult]
