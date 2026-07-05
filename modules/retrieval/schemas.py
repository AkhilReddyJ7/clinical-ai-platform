import uuid

from pydantic import BaseModel, Field, field_validator


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


class AnswerQueryIn(BaseModel):
    # max_length bounds cost at the validation layer (422) rather than by
    # silent truncation -- a question is not a document; truncating it
    # changes its meaning (ADR-0038).
    question: str = Field(min_length=1, max_length=2_000)
    # None -> settings.retrieval_default_top_k; upper bound enforced at
    # the router against settings.retrieval_max_top_k, same as
    # RetrievalQueryIn (ADR-0035).
    top_k: int | None = Field(default=None, ge=1)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class AnswerOut(BaseModel):
    question: str
    answer: str
    insufficient_context: bool
    # Reuses the /retrieval/query chunk contract (including chunk_text --
    # the same already-exposed surface, no new PHI boundary, ADR-0038).
    citations: list[RetrievedChunkOut]
