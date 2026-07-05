import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from starlette.concurrency import run_in_threadpool

from apps.api.dependencies import get_retrieval_service
from modules.retrieval.schemas import RetrievalQueryIn, RetrievalQueryOut, RetrievedChunkOut
from modules.retrieval.service import RetrievalService
from modules.auth.api_key import require_api_key
from shared.config.settings import get_settings

router = APIRouter(prefix="/retrieval", tags=["retrieval"], dependencies=[Depends(require_api_key)])


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
    settings = get_settings()
    top_k = body.top_k or settings.retrieval_default_top_k
    if top_k > settings.retrieval_max_top_k:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"top_k must not exceed {settings.retrieval_max_top_k}",
        )

    results = await run_in_threadpool(retrieval_service.query, query_text=body.query, top_k=top_k)
    return RetrievalQueryOut(
        query=body.query,
        results=[
            RetrievedChunkOut(
                document_id=uuid.UUID(chunk.document_id),
                extraction_id=uuid.UUID(chunk.extraction_id),
                chunk_index=chunk.chunk_index,
                chunk_text=chunk.chunk_text,
                score=chunk.score,
            )
            for chunk in results
        ],
    )
