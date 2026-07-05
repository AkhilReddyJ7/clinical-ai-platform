"""Bulk reindex CLI (ADR-0035).

Indexes every currently-`validated` document's latest ExtractionResult
(the same "current result" definition GET /documents/{id}/result uses,
ADR-0031 section 4) into the vector store. Runs directly and
synchronously -- unlike scripts/run_backfill.py, this bypasses the
Job/queue state machine entirely: indexing is cheap (no LLM call) and
isn't part of it. Delete-then-upsert (RetrievalService.index_extraction)
makes every run idempotent regardless of how many times it's re-run --
no "already indexed" check is needed.

Usage:
    uv run python -m scripts.run_reindex
    uv run python -m scripts.run_reindex --document-id <uuid>
"""

import argparse
import uuid
from functools import lru_cache

from sqlalchemy import select

from modules.ingestion.models import Document, DocumentStatus
from modules.ocr.models import ExtractionResult
from modules.retrieval.base import EmbeddingPipeline, VectorStore
from modules.retrieval.chroma_store import ChromaVectorStore
from modules.retrieval.fastembed_embeddings import FastEmbedEmbeddingPipeline
from modules.retrieval.service import RetrievalService
from shared.config.settings import get_settings
from shared.database.session import AsyncSessionLocal


# Mirrors apps/api/dependencies.py / modules/processing/worker.py's own
# composition roots (modules/ may not depend on apps/, ADR-0001).
@lru_cache
def _embedding_pipeline() -> EmbeddingPipeline:
    settings = get_settings()
    return FastEmbedEmbeddingPipeline(
        model_name=settings.embedding_model_name,
        cache_dir=settings.embedding_model_cache_dir,
    )


@lru_cache
def _vector_store() -> VectorStore:
    settings = get_settings()
    return ChromaVectorStore(
        host=settings.chroma_host,
        port=settings.chroma_port,
        collection_name=settings.chroma_collection_name,
    )


@lru_cache
def _retrieval_service() -> RetrievalService:
    settings = get_settings()
    return RetrievalService(
        embedding_pipeline=_embedding_pipeline(),
        vector_store=_vector_store(),
        chunk_size_chars=settings.retrieval_chunk_size_chars,
        overlap_chars=settings.retrieval_chunk_overlap_chars,
    )


async def _candidates(*, document_id: uuid.UUID | None) -> list[tuple[uuid.UUID, uuid.UUID, str]]:
    """Returns (document_id, extraction_id, raw_text) for each currently-
    VALIDATED document's latest ExtractionResult -- the same "current
    result" definition GET /documents/{id}/result uses (ADR-0031 section 4).
    """
    async with AsyncSessionLocal() as db:
        stmt = select(Document.id).where(Document.status == DocumentStatus.VALIDATED)
        if document_id is not None:
            stmt = stmt.where(Document.id == document_id)
        document_ids = list((await db.execute(stmt)).scalars().all())

        candidates: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        for doc_id in document_ids:
            extraction = await db.scalar(
                select(ExtractionResult)
                .where(ExtractionResult.document_id == doc_id)
                .order_by(ExtractionResult.created_at.desc())
            )
            if extraction is not None:
                candidates.append((doc_id, extraction.id, extraction.raw_text))
        return candidates


def _run_reindex(
    candidates: list[tuple[uuid.UUID, uuid.UUID, str]], retrieval_service: RetrievalService
) -> tuple[int, int]:
    indexed = 0
    failed = 0
    for document_id, extraction_id, raw_text in candidates:
        try:
            chunks = retrieval_service.index_extraction(
                document_id=document_id, extraction_id=extraction_id, raw_text=raw_text
            )
        except Exception:
            failed += 1
            print(f"failed to index document={document_id} extraction={extraction_id}")
            continue
        indexed += 1
        print(f"indexed document={document_id} extraction={extraction_id} chunks={chunks}")
    return indexed, failed


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--document-id", type=uuid.UUID, default=None)
    args = parser.parse_args(argv)

    candidates = await _candidates(document_id=args.document_id)
    print(f"{len(candidates)} candidate document(s) to index")

    indexed, failed = _run_reindex(candidates, _retrieval_service())
    print(f"indexed={indexed} failed={failed}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
