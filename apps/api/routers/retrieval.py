import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from apps.api.dependencies import get_answer_generator, get_retrieval_service
from modules.retrieval.answer_base import (
    AnswerGenerationError,
    AnswerGenerationNotConfiguredError,
    AnswerGenerator,
)
from modules.retrieval.base import RetrievedChunk
from modules.retrieval.schemas import (
    AnswerOut,
    AnswerQueryIn,
    RetrievalQueryIn,
    RetrievalQueryOut,
    RetrievedChunkOut,
)
from modules.retrieval.service import RetrievalService
from modules.auth.api_key import require_api_key
from shared.config.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/retrieval", tags=["retrieval"], dependencies=[Depends(require_api_key)])

_EMPTY_CORPUS_ANSWER = "No indexed document content matched the question."


def _resolve_top_k(requested: int | None) -> int:
    settings = get_settings()
    top_k = requested or settings.retrieval_default_top_k
    if top_k > settings.retrieval_max_top_k:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"top_k must not exceed {settings.retrieval_max_top_k}",
        )
    return top_k


def _chunk_out(chunk: RetrievedChunk) -> RetrievedChunkOut:
    return RetrievedChunkOut(
        document_id=uuid.UUID(chunk.document_id),
        extraction_id=uuid.UUID(chunk.extraction_id),
        chunk_index=chunk.chunk_index,
        chunk_text=chunk.chunk_text,
        score=chunk.score,
    )


@router.post("/query", response_model=RetrievalQueryOut)
async def query_retrieval(
    body: RetrievalQueryIn,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> RetrievalQueryOut:
    """GET-the-corpus-back read over already-indexed chunks (ADR-0035).
    No PHI check on the query text itself -- PHI safety here is about
    what gets indexed (already gated to VALIDATED documents only), not
    what an already-authenticated caller types; the response can only
    ever surface text already readable via GET /documents/{id}/result
    under ADR-0026's flat access model.
    """
    top_k = _resolve_top_k(body.top_k)
    results = await run_in_threadpool(retrieval_service.query, query_text=body.query, top_k=top_k)
    return RetrievalQueryOut(query=body.query, results=[_chunk_out(chunk) for chunk in results])


@router.post("/answer", response_model=AnswerOut)
async def answer_question(
    body: AnswerQueryIn,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> AnswerOut:
    """Grounded Q&A over the indexed corpus (ADR-0038): retrieve, then
    generate an answer cited against the retrieved chunks. The first
    synchronous in-request LLM call in the codebase -- a read, not a
    state mutation, so it bypasses the worker/job queue deliberately.
    ADR-0035's no-PHI-check-on-query reasoning extends here: the answer
    is grounded exclusively in chunks that are already indexed (VALIDATED
    documents only) and already readable via POST /retrieval/query.
    """
    top_k = _resolve_top_k(body.top_k)

    chunks = await run_in_threadpool(retrieval_service.query, query_text=body.question, top_k=top_k)
    if not chunks:
        # Deterministic abstention without an LLM call: an empty corpus
        # is a normal state, there is nothing to ground an answer in, and
        # skipping the call saves a paid API round trip while keeping the
        # response shape uniform.
        return AnswerOut(
            question=body.question,
            answer=_EMPTY_CORPUS_ANSWER,
            insufficient_context=True,
            citations=[],
        )

    try:
        generated = await run_in_threadpool(
            answer_generator.generate, question=body.question, chunks=chunks
        )
    except AnswerGenerationNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="answer generation is not configured",
        )
    except AnswerGenerationError:
        # Full provider detail stays server-side only: exception text from
        # the SDK may embed request content and must not leak to callers.
        logger.exception("answer generation failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="answer generation failed"
        )

    # The GeneratedAnswer contract guarantees in-range, deduplicated
    # indices, so this lookup needs no re-validation.
    return AnswerOut(
        question=body.question,
        answer=generated.answer,
        insufficient_context=generated.insufficient_context,
        citations=[_chunk_out(chunks[i]) for i in generated.cited_chunk_indices],
    )
