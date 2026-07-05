"""End-to-end: a real classified failure from run_processing_pipeline,
dispatched through the actual worker loop, lands on the outcome ADR-0023
says it should — not a hand-rolled stand-in for either half.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import patch

import anthropic
import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import modules.processing.worker as worker_module
from modules.extraction.anthropic_extractor import AnthropicFieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.mock import MockExtractionPipeline
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import run_processing_pipeline
from modules.processing.worker import start_worker, stop_worker
from modules.retrieval.mock import InMemoryVectorStore, MockEmbeddingPipeline
from modules.retrieval.service import RetrievalService
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
POLL_INTERVAL = 0.01


async def _make_document_and_job(
    session_factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage
) -> Job:
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, b"a clinical note with some content")

    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="note.txt",
            content_type="text/plain",
            size_bytes=10,
            storage_key=storage_key,
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()

        job = Job(document_id=document.id, status=JobStatus.RUNNING)
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job


def _fake_claim_once(
    job: Job, drained: asyncio.Event
) -> Callable[[AsyncSession], Awaitable[Job | None]]:
    claimed = {"done": False}

    async def fake_claim_next_job(session: AsyncSession) -> Job | None:
        if not claimed["done"]:
            claimed["done"] = True
            return job
        drained.set()
        return None

    return fake_claim_next_job


@pytest.mark.asyncio
async def test_transient_pipeline_failure_leaves_job_retrying(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="test-key", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )
    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=httpx.Response(429, request=_REQUEST), body=None
    )

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            with patch.object(
                field_extraction_pipeline._client.messages, "create", side_effect=rate_limit_error
            ):
                return await run_processing_pipeline(
                    claimed_job,
                    db=db,
                    storage=storage,
                    extraction_pipeline=MockExtractionPipeline(),
                    field_extraction_pipeline=field_extraction_pipeline,
                    phi_validator=PHIDetectionValidator(),
                    validation_pipeline=CompositeValidationPipeline(
                        [RequiredFieldsValidator(), PHIDetectionValidator()]
                    ),
                    retrieval_service=RetrievalService(
                        embedding_pipeline=MockEmbeddingPipeline(),
                        vector_store=InMemoryVectorStore(),
                    ),
                )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.RETRYING
        assert stored.retry_count == 1


@pytest.mark.asyncio
async def test_terminal_pipeline_failure_leaves_job_failed(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    # No API key configured -> a terminal FieldExtractionError.
    field_extraction_pipeline = AnthropicFieldExtractionPipeline(
        api_key="", model="claude-haiku-4-5", timeout_seconds=5.0, max_input_chars=1000
    )

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=MockExtractionPipeline(),
                field_extraction_pipeline=field_extraction_pipeline,
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
                retrieval_service=RetrievalService(
                    embedding_pipeline=MockEmbeddingPipeline(), vector_store=InMemoryVectorStore()
                ),
            )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.FAILED
        assert stored.last_error is not None
        assert "API key" in stored.last_error


@pytest.mark.asyncio
async def test_successful_pipeline_leaves_job_completed(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    async def process_job_fn(claimed_job: Job) -> object:
        async with session_factory() as db:
            return await run_processing_pipeline(
                claimed_job,
                db=db,
                storage=storage,
                extraction_pipeline=MockExtractionPipeline(),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
                retrieval_service=RetrievalService(
                    embedding_pipeline=MockEmbeddingPipeline(), vector_store=InMemoryVectorStore()
                ),
            )

    drained = asyncio.Event()
    monkeypatch.setattr(worker_module, "claim_next_job", _fake_claim_once(job, drained))

    task = await start_worker(
        session_factory, process_job_fn=process_job_fn, poll_interval_seconds=POLL_INTERVAL
    )
    await asyncio.wait_for(drained.wait(), timeout=2)
    await stop_worker(task)

    async with session_factory() as session:
        stored = await session.get(Job, job.id)
        assert stored is not None
        assert stored.status == JobStatus.COMPLETED
