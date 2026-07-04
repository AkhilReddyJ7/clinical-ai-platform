import logging
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from modules.extraction.base import FieldExtractionOutput, FieldExtractionPipeline
from modules.extraction.mock import MockFieldExtractionPipeline
from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import LocalFileStorage
from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.mock import MockExtractionPipeline
from modules.processing.errors import TerminalProcessingError
from modules.processing.metrics import metrics
from modules.processing.models import Job, JobStatus
from modules.processing.pipeline import run_processing_pipeline
from modules.validation.composite import CompositeValidationPipeline
from modules.validation.phi import PHIDetectionValidator
from modules.validation.rules import RequiredFieldsValidator


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    metrics.reset()


class _FailingOCR(ExtractionPipeline):
    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        raise ExtractionError("corrupted input bytes")


class _FakeOCR(ExtractionPipeline):
    """Returns literal text, unlike MockExtractionPipeline (which
    synthesizes raw_text from a hash of the bytes, ignoring their actual
    content) — needed here to reliably trigger the PHI gate.
    """

    def __init__(self, raw_text: str, confidence: float = 1.0) -> None:
        self._raw_text = raw_text
        self._confidence = confidence

    def extract(self, *, data: bytes, content_type: str) -> ExtractionOutput:
        return ExtractionOutput(raw_text=self._raw_text, confidence=self._confidence)


class _NoFieldsExtraction(FieldExtractionPipeline):
    def extract_fields(self, *, raw_text: str) -> FieldExtractionOutput:
        return FieldExtractionOutput(fields={}, confidence=0.0)


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


async def _run_success(
    session_factory: async_sessionmaker[AsyncSession], storage: LocalFileStorage, job: Job
) -> None:
    async with session_factory() as db:
        await run_processing_pipeline(
            job,
            db=db,
            storage=storage,
            extraction_pipeline=MockExtractionPipeline(),
            field_extraction_pipeline=MockFieldExtractionPipeline(),
            phi_validator=PHIDetectionValidator(),
            validation_pipeline=CompositeValidationPipeline(
                [RequiredFieldsValidator(), PHIDetectionValidator()]
            ),
        )


@pytest.mark.asyncio
async def test_each_stage_logs_completion_with_job_and_document_ids(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        await _run_success(session_factory, storage, job)

    stage_logs = [r.message for r in caplog.records if "stage complete" in r.message]
    stages_logged = {msg.split("stage=")[1].split()[0] for msg in stage_logs}
    assert stages_logged == {"ocr", "field_extraction", "validation", "pipeline_total"}
    for message in stage_logs:
        assert f"job_id={job.id}" in message
        assert f"document_id={job.document_id}" in message
        assert "duration_seconds=" in message


@pytest.mark.asyncio
async def test_stage_timings_are_recorded_in_metrics(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    await _run_success(session_factory, storage, job)

    for stage in ("ocr", "field_extraction", "validation", "pipeline_total"):
        summary = metrics.stage_summary(stage)
        assert summary is not None, f"expected a recorded duration for stage={stage}"
        assert summary.count == 1
        assert summary.avg_seconds >= 0.0


@pytest.mark.asyncio
async def test_confidence_summary_is_logged_for_a_completed_job(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        await _run_success(session_factory, storage, job)

    confidence_logs = [r.message for r in caplog.records if "confidence summary" in r.message]
    assert len(confidence_logs) == 1
    message = confidence_logs[0]
    assert f"job_id={job.id}" in message
    assert f"document_id={job.document_id}" in message
    assert "document_confidence=" in message
    assert "low_confidence_field_count=" in message
    assert "field_confidence=[" in message


@pytest.mark.asyncio
async def test_ocr_stage_logs_and_records_duration_even_on_failure(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    job = await _make_document_and_job(session_factory, storage)

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        async with session_factory() as db:
            with pytest.raises(TerminalProcessingError):
                await run_processing_pipeline(
                    job,
                    db=db,
                    storage=storage,
                    extraction_pipeline=_FailingOCR(),
                    field_extraction_pipeline=MockFieldExtractionPipeline(),
                    phi_validator=PHIDetectionValidator(),
                    validation_pipeline=CompositeValidationPipeline(
                        [RequiredFieldsValidator(), PHIDetectionValidator()]
                    ),
                )

    stage_logs = [r.message for r in caplog.records if "stage=ocr" in r.message]
    assert len(stage_logs) == 1
    assert metrics.stage_summary("ocr") is not None
    # A failed stage never reaches field_extraction/validation/pipeline_total.
    assert metrics.stage_summary("field_extraction") is None
    assert metrics.stage_summary("pipeline_total") is None


@pytest.mark.asyncio
async def test_phi_detected_still_logs_confidence_summary_and_pipeline_total(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    storage = LocalFileStorage(tmp_path / "uploads")
    storage_key = f"{uuid.uuid4()}/note.txt"
    storage.save(storage_key, b"patient ssn 123-45-6789")

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

    with caplog.at_level(logging.INFO, logger="clinical-ai-platform"):
        async with session_factory() as db:
            await run_processing_pipeline(
                job,
                db=db,
                storage=storage,
                extraction_pipeline=_FakeOCR("patient ssn 123-45-6789 needs follow-up"),
                field_extraction_pipeline=MockFieldExtractionPipeline(),
                phi_validator=PHIDetectionValidator(),
                validation_pipeline=CompositeValidationPipeline(
                    [RequiredFieldsValidator(), PHIDetectionValidator()]
                ),
            )

    assert any("confidence summary" in r.message for r in caplog.records)
    assert any("stage=pipeline_total" in r.message for r in caplog.records)
    # PHI-detected halts before field extraction — that stage never ran.
    assert metrics.stage_summary("field_extraction") is None
