import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.ingestion.models import Document, DocumentStatus
from modules.processing.models import Job, JobStatus


@pytest.mark.asyncio
async def test_job_defaults_to_queued_and_links_to_its_document(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        document = Document(
            id=uuid.uuid4(),
            filename="report.txt",
            content_type="text/plain",
            size_bytes=3,
            storage_key="doc/report.txt",
            status=DocumentStatus.UPLOADED,
        )
        session.add(document)
        await session.commit()

        job = Job(document_id=document.id)
        session.add(job)
        await session.commit()
        await session.refresh(job)

        assert job.status == JobStatus.QUEUED
        assert job.document_id == document.id

        stored = await session.scalar(select(Job).where(Job.id == job.id))
        assert stored is not None
        assert stored.status == JobStatus.QUEUED
