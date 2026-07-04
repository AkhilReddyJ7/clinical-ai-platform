"""Concurrent job-claiming behavior against real Postgres.

SQLite (the fast suite's engine, per ADR-0004) silently compiles away
``FOR UPDATE SKIP LOCKED`` — it can prove claim_next_job's *logic*
(tests/unit/test_job_claiming.py) but not the row-locking guarantee that
makes concurrent claims safe. ADR-0004 names this exact gap and its
resolution: "add a Postgres-backed integration test tier" once the schema
adopts a genuinely Postgres-specific feature. This module is that tier.

Requires a reachable Postgres (e.g. `docker compose up -d postgres`) at
`Settings.database_url`; skips cleanly if none is running, so it never
breaks the fast, infrastructure-free CI `test` job.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus
from modules.processing.repository import claim_next_job
from shared.database.base import Base
from shared.database.session import settings


@pytest_asyncio.fixture
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except (OSError, OperationalError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres not reachable at {settings.database_url}: {exc}")
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_claims_never_double_claim_the_same_job(
    pg_engine: AsyncEngine,
) -> None:
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    job_count = 5
    claimant_count = 10  # more claimants than jobs, so some must see an empty queue

    async with session_factory() as setup_session:
        document = Document(
            id=uuid.uuid4(),
            filename="report.txt",
            content_type="text/plain",
            size_bytes=3,
            storage_key=f"{uuid.uuid4()}/report.txt",
            status=DocumentStatus.PROCESSING,
        )
        setup_session.add(document)
        await setup_session.commit()

        jobs = [Job(document_id=document.id, status=JobStatus.QUEUED) for _ in range(job_count)]
        setup_session.add_all(jobs)
        await setup_session.commit()
        job_ids = {job.id for job in jobs}

    try:

        async def _claim_with_own_session() -> uuid.UUID | None:
            async with session_factory() as session:
                claimed = await claim_next_job(session)
                return claimed.id if claimed is not None else None

        results = await asyncio.gather(*(_claim_with_own_session() for _ in range(claimant_count)))

        claimed_ids = [job_id for job_id in results if job_id is not None]
        empty_results = [job_id for job_id in results if job_id is None]

        # Every successful claim is one of our jobs, no job claimed twice,
        # and every job got claimed exactly once despite the race.
        assert len(claimed_ids) == job_count
        assert set(claimed_ids) == job_ids
        assert len(empty_results) == claimant_count - job_count

        async with session_factory() as verify_session:
            for job_id in job_ids:
                job = await verify_session.get(Job, job_id)
                assert job is not None
                assert job.status == JobStatus.RUNNING
    finally:
        async with session_factory() as cleanup_session:
            for job_id in job_ids:
                job = await cleanup_session.get(Job, job_id)
                if job is not None:
                    await cleanup_session.delete(job)
            doc = await cleanup_session.get(Document, document.id)
            if doc is not None:
                await cleanup_session.delete(doc)
            await cleanup_session.commit()
