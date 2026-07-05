import uuid
from datetime import datetime

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
    # ADR-0036: count of every ExtractionResult row below
    # settings.low_confidence_threshold -- every recorded *attempt*, not
    # deduplicated per document. A trend/volume signal, distinct from
    # LowConfidenceDocumentOut below (which reports only each document's
    # *current* attempt) -- a document reprocessed to a good result still
    # counts its earlier low-confidence attempt here.
    low_confidence_count: int


class MetricsOut(BaseModel):
    jobs: JobMetricsOut
    documents: DocumentMetricsOut
    confidence: ConfidenceMetricsOut


class LowConfidenceDocumentOut(BaseModel):
    """One document whose *current* (latest-by-created_at) ExtractionResult
    is below settings.low_confidence_threshold (ADR-0036) -- operational
    triage, distinct from ConfidenceMetricsOut.low_confidence_count's
    historical/trend total.
    """

    document_id: uuid.UUID
    extraction_id: uuid.UUID
    confidence: float
    created_at: datetime
