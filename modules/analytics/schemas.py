from pydantic import BaseModel


class JobMetricsOut(BaseModel):
    by_status: dict[str, int]
    avg_retry_count: float
    max_retry_count: int


class DocumentMetricsOut(BaseModel):
    by_status: dict[str, int]


class ConfidenceMetricsOut(BaseModel):
    count: int
    min: float | None
    avg: float | None
    max: float | None


class MetricsOut(BaseModel):
    jobs: JobMetricsOut
    documents: DocumentMetricsOut
    confidence: ConfidenceMetricsOut
