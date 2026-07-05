import uuid

from pydantic import BaseModel, Field


class RetrievalQueryIn(BaseModel):
    query: str
    # None -> settings.retrieval_default_top_k; upper bound enforced
    # against settings.retrieval_max_top_k at the router, not hardcoded
    # here, so the two stay in one place (ADR-0035).
    top_k: int | None = Field(default=None, ge=1)


class RetrievedChunkOut(BaseModel):
    document_id: uuid.UUID
    extraction_id: uuid.UUID
    chunk_index: int
    chunk_text: str
    score: float


class RetrievalQueryOut(BaseModel):
    query: str
    results: list[RetrievedChunkOut]
