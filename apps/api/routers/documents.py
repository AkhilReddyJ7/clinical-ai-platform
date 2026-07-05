import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_storage
from apps.api.schemas import (
    DocumentHistoryEntryOut,
    DocumentHistoryOut,
    DocumentListOut,
    ProcessEnqueuedOut,
    ProcessingStatusOut,
    ReprocessIn,
)
from modules.audit.models import AuditAction
from modules.audit.service import record_action
from modules.auth.api_key import require_api_key
from modules.ingestion import service as ingestion_service
from modules.ingestion.models import DocumentStatus
from modules.ingestion.schemas import DocumentOut
from modules.ingestion.storage import StorageBackend
from modules.ocr.models import ExtractionResult
from modules.ocr.schemas import ExtractionResultOut
from modules.processing.models import Job, JobStatus
from modules.processing.repository import enqueue_job, force_reprocess_job
from modules.processing.state_machine import IllegalTransitionError
from modules.validation.models import ValidationResult
from modules.validation.schemas import ValidationResultOut
from shared.config.settings import get_settings
from shared.database.session import get_db
from shared.logging.logger import logger

settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "text/plain"}
_ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.RETRYING)

_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024  # 1 MiB


async def _read_upload_within_limit(file: UploadFile, max_bytes: int) -> bytes:
    """Reads an UploadFile in chunks, rejecting as soon as the running
    total exceeds max_bytes. Unlike `await file.read()` — which
    materializes the entire upload into memory before any size check can
    run — this never buffers more than roughly max_bytes plus one chunk.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="file exceeds maximum upload size",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    caller: str = Depends(require_api_key),
) -> DocumentOut:
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported content type: {file.content_type}",
        )

    data = await _read_upload_within_limit(file, settings.max_upload_size_bytes)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty file upload")

    document = await ingestion_service.register_document(
        db,
        storage,
        filename=file.filename or "unnamed",
        content_type=file.content_type,
        data=data,
    )
    logger.info(
        "document uploaded id=%s filename=%s caller=%s", document.id, document.filename, caller
    )
    await record_action(
        db, caller=caller, action=AuditAction.DOCUMENT_UPLOADED, document_id=document.id
    )
    return DocumentOut.model_validate(document)


@router.get("", response_model=DocumentListOut)
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DocumentListOut:
    documents, total = await ingestion_service.list_documents(db, limit=limit, offset=offset)
    return DocumentListOut(
        items=[DocumentOut.model_validate(doc) for doc in documents],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> DocumentOut:
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return DocumentOut.model_validate(document)


@router.post(
    "/{document_id}/process",
    response_model=ProcessEnqueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def process_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_api_key),
) -> ProcessEnqueuedOut:
    """Enqueues a processing job and returns immediately (ADR-0022) --
    no longer runs the pipeline inline. modules/processing/worker.py's
    background loop claims and runs the job; GET .../result reports
    progress and, once terminal, the outcome.
    """
    try:
        job = await enqueue_job(db, document_id)
    except IllegalTransitionError:
        # Document exists (enqueue_job's locked read confirmed that) but
        # isn't in a legal starting state: either an active job already
        # exists (document status == processing/extracted), or the
        # document is already validated (ADR-0022's 409 case).
        document = await ingestion_service.get_document(db, document_id)
        reason = (
            "document is already validated"
            if document is not None and document.status == DocumentStatus.VALIDATED
            else "document already has an active processing job"
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{reason}; see GET /documents/{document_id}/result for current status",
        ) from None

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    logger.info("job enqueued id=%s document_id=%s caller=%s", job.id, document_id, caller)
    await record_action(
        db,
        caller=caller,
        action=AuditAction.JOB_ENQUEUED,
        document_id=document_id,
        job_id=job.id,
    )
    return ProcessEnqueuedOut(
        document_id=document_id,
        job_id=job.id,
        job_status=job.status,
        attempt_number=job.attempt_number,
        trigger=job.trigger,
        trigger_note=job.trigger_note,
    )


@router.post(
    "/{document_id}/reprocess",
    response_model=ProcessEnqueuedOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_document(
    document_id: uuid.UUID,
    body: ReprocessIn = ReprocessIn(),
    db: AsyncSession = Depends(get_db),
    caller: str = Depends(require_api_key),
) -> ProcessEnqueuedOut:
    """Forces reprocessing of an already-validated document (ADR-0032) --
    the one deliberate, audited bypass of validated -> processing
    ADR-0020 left disallowed by default. Distinct from /process: this is
    the only entry point that can move a validated document back to
    processing; /process remains for the uploaded/failed cases.
    """
    try:
        job = await force_reprocess_job(db, document_id, trigger_note=body.trigger_note)
    except IllegalTransitionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "document is not currently validated; see "
                f"GET /documents/{document_id}/result for current status"
            ),
        ) from None

    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    logger.info(
        "forced reprocess job=%s document_id=%s caller=%s trigger_note=%s",
        job.id,
        document_id,
        caller,
        body.trigger_note,
    )
    await record_action(
        db,
        caller=caller,
        action=AuditAction.FORCED_REPROCESS,
        document_id=document_id,
        job_id=job.id,
    )
    return ProcessEnqueuedOut(
        document_id=document_id,
        job_id=job.id,
        job_status=job.status,
        attempt_number=job.attempt_number,
        trigger=job.trigger,
        trigger_note=job.trigger_note,
    )


@router.get("/{document_id}/result", response_model=ProcessingStatusOut)
async def get_processing_result(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingStatusOut:
    """The canonical status/result endpoint (ADR-0022): the one place a
    caller looks to answer both "what's the status" and "what's the
    result". Exactly one outcome is non-200 (document not found) --
    every other case, including "never submitted" and "processing
    failed", is 200 with the document's current state discriminated in
    the body.
    """
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    # Document status is the authoritative signal (ADR-0020: it answers
    # what's currently true from a caller's perspective) -- checked
    # first, not the Job table, because a job's own status can lag the
    # document's by one write: run_processing_pipeline moves the document
    # to its terminal status as its last step, but the job itself is only
    # marked `completed` in a *separate*, later transaction (worker.py,
    # after process_job_fn returns) -- see
    # test_document_reaches_terminal_status_before_the_job_does. Deciding
    # from the Job table first would occasionally report "processing" for
    # a document that's already validated/failed.
    if document.status in (DocumentStatus.VALIDATED, DocumentStatus.FAILED):
        extraction = await db.scalar(
            select(ExtractionResult)
            .where(ExtractionResult.document_id == document_id)
            .order_by(ExtractionResult.created_at.desc())
        )
        validation = await db.scalar(
            select(ValidationResult)
            .where(ValidationResult.document_id == document_id)
            .order_by(ValidationResult.created_at.desc())
        )
        return ProcessingStatusOut(
            document=DocumentOut.model_validate(document),
            extraction=ExtractionResultOut.model_validate(extraction) if extraction else None,
            validation=ValidationResultOut.model_validate(validation) if validation else None,
        )

    active_job = await db.scalar(
        select(Job)
        .where(Job.document_id == document_id, Job.status.in_(_ACTIVE_JOB_STATUSES))
        .order_by(Job.created_at.desc())
    )
    return ProcessingStatusOut(
        document=DocumentOut.model_validate(document),
        job_status=active_job.status if active_job is not None else None,
    )


@router.get("/{document_id}/history", response_model=DocumentHistoryOut)
async def get_document_history(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> DocumentHistoryOut:
    """Every processing attempt for a document, in order (ADR-0031) --
    not paginated, since a document's attempt count is naturally small,
    unlike the global /audit or /metrics collections. A job with no
    matching result row (e.g. a worker-level failure that never reached
    pipeline.py's _persist_failure) simply reports null pipeline_version/
    confidence/is_valid -- not every Job has a corresponding result.
    """
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    jobs = (
        (
            await db.execute(
                select(Job).where(Job.document_id == document_id).order_by(Job.attempt_number.asc())
            )
        )
        .scalars()
        .all()
    )
    job_ids = [job.id for job in jobs]
    extraction_by_job = {
        e.job_id: e
        for e in (
            await db.execute(select(ExtractionResult).where(ExtractionResult.job_id.in_(job_ids)))
        )
        .scalars()
        .all()
    }
    validation_by_job = {
        v.job_id: v
        for v in (
            await db.execute(select(ValidationResult).where(ValidationResult.job_id.in_(job_ids)))
        )
        .scalars()
        .all()
    }

    items = [
        DocumentHistoryEntryOut(
            job_id=job.id,
            attempt_number=job.attempt_number,
            trigger=job.trigger,
            trigger_note=job.trigger_note,
            job_status=job.status,
            created_at=job.created_at,
            pipeline_version=(
                extraction_by_job[job.id].pipeline_version if job.id in extraction_by_job else None
            ),
            confidence=(
                extraction_by_job[job.id].confidence if job.id in extraction_by_job else None
            ),
            is_valid=(validation_by_job[job.id].is_valid if job.id in validation_by_job else None),
        )
        for job in jobs
    ]
    return DocumentHistoryOut(document_id=document_id, items=items)
