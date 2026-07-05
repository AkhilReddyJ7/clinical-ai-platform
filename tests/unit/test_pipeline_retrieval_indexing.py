"""ADR-0035: retrieval indexing inside run_processing_pipeline is
VALIDATED-only (the PHI safety boundary) and non-fatal (a Chroma/indexing
failure must never fail the surrounding document/job).
"""

import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.extraction.base import FieldExtractionOutput, FieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.base import ExtractionOutput, ExtractionPipeline
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import ProcessingResult, run_processing_pipeline
from modules.retrieval.base import EmbeddingPipeline, RetrievedChunk, VectorStore
from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline
from modules.retrieval.service import RetrievalService
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator


class _FakeOCR(ExtractionPipeline):
    def __init__(self, raw_text: str) -> None:
        self._raw_text = raw_text

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        return ExtractionOutput(raw_text=self._raw_text, confidence=1.0)


class _NoFieldsExtraction(FieldExtractionPipeline):
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        return FieldExtractionOutput(fields={}, confidence=0.0)


class _RaisingVectorStore(VectorStore):
    """Simulates a Chroma outage -- every operation raises."""

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, str | int]],
    ) -> None:
        raise RuntimeError("simulated Chroma outage")

    def delete(self, *, where: dict[str, str]) -> None:
        raise RuntimeError("simulated Chroma outage")

    def query(self, *, query_embedding: list[float], top_k: int) -> list[RetrievedChunk]:
        raise RuntimeError("simulated Chroma outage")


async def _make_document_and_job(
    session_factory: async_sessionmaker[AsyncSession],
    storage: LocalFileStorage,
    *,
    data: bytes = b"some clinical note text",
) -> tuple[uuid.UUID, uuid.UUID]:
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, data)

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type="text/plain",
            size_bytes=len(data),
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()

        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()
        return document.id, job.id


def _retrieval_service(vector_store: VectorStore | None = None) -> RetrievalService:
    embedding_pipeline: EmbeddingPipeline = MockEmbeddingPipeline()
    store: VectorStore = vector_store or InMemoryVectorStore()
    return RetrievalService(embedding_pipeline=embedding_pipeline, vector_store=store)


async def _run(
    job: Job,
    db: AsyncSession,
    storage: LocalFileStorage,
    *,
    extraction_pipeline: ExtractionPipeline,
    field_extraction_pipeline: FieldExtractionPipeline,
    retrieval_service: RetrievalService,
) -> ProcessingResult:
    return await run_processing_pipeline(
        job,
        db=db,
        storage=storage,
        extraction_pipeline=extraction_pipeline,
        field_extraction_pipeline=field_extraction_pipeline,
        phi_validator=PHIDetectionValidator(),
        validation_pipeline=CompositeValidationPipeline(
            [RequiredFieldsValidator(), PHIDetectionValidator()]
        ),
        retrieval_service=retrieval_service,
    )


@pytest.mark.asyncio
async def test_validated_document_gets_indexed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)
    store = InMemoryVectorStore()

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("a clean clinical note with no PHI-shaped strings"),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            retrieval_service=_retrieval_service(store),
        )

    assert result.is_valid is True
    hits = store.query(
        query_embedding=MockEmbeddingPipeline().embed(["clean clinical note"])[0], top_k=5
    )
    assert len(hits) == 1
    assert hits[0].document_id == str(document_id)


@pytest.mark.asyncio
async def test_phi_flagged_document_is_never_indexed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    _, job_id = await _make_document_and_job(session_factory, storage)
    store = InMemoryVectorStore()

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("patient ssn 123-45-6789 needs follow-up"),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            retrieval_service=_retrieval_service(store),
        )

    assert result.is_valid is False
    assert store.query(query_embedding=MockEmbeddingPipeline().embed(["ssn"])[0], top_k=5) == []


@pytest.mark.asyncio
async def test_missing_required_field_document_is_never_indexed(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    _, job_id = await _make_document_and_job(session_factory, storage)
    store = InMemoryVectorStore()

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("a clinical note with no structured fields at all"),
            field_extraction_pipeline=_NoFieldsExtraction(),
            retrieval_service=_retrieval_service(store),
        )

    assert result.is_valid is False
    assert (
        store.query(query_embedding=MockEmbeddingPipeline().embed(["clinical note"])[0], top_k=5)
        == []
    )


@pytest.mark.asyncio
async def test_indexing_failure_does_not_fail_the_surrounding_job(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    """The load-bearing test for ADR-0035's non-fatal guarantee: a
    Chroma/indexing failure must never propagate out of
    run_processing_pipeline -- the document still reaches `validated`.
    """
    storage = LocalFileStorage(tmp_path / "uploads")
    document_id, job_id = await _make_document_and_job(session_factory, storage)

    async with session_factory() as db:
        job = await db.get(Job, job_id)
        assert job is not None
        result = await _run(
            job,
            db,
            storage,
            extraction_pipeline=_FakeOCR("a clean clinical note with no PHI-shaped strings"),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            retrieval_service=_retrieval_service(_RaisingVectorStore()),
        )

    assert result.is_valid is True

    async with session_factory() as db:
        document = await db.get(Document, document_id)
        assert document is not None
        assert document.status == DocumentStatus.VALIDATED
