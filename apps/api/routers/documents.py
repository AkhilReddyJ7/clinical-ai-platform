import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from apps.api.dependencies import (
    get_extraction_pipeline,
    get_field_extraction_pipeline,
    get_phi_validator,
    get_storage,
    get_validation_pipeline,
)
from apps.api.schemas import DocumentListOut, ProcessingResultOut
from modules.auth.api_key import require_api_key
from modules.extraction.base import FieldExtractionError, FieldExtractionPipeline
from modules.ingestion import service as ingestion_service
from modules.ingestion.models import DocumentStatus
from modules.ingestion.schemas import DocumentOut
from modules.ingestion.storage import StorageBackend
from modules.ocr.base import ExtractionError, ExtractionOutput, ExtractionPipeline
from modules.ocr.models import ExtractionResult
from modules.ocr.schemas import ExtractionResultOut
from modules.validation.base import ValidationPipeline
from modules.validation.models import ValidationResult
from modules.validation.schemas import ValidationResultOut
from shared.config.settings import get_settings
from shared.database.session import get_db
from shared.logging.logger import logger

settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_api_key)])

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "text/plain"}

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
    logger.info("document uploaded id=%s filename=%s", document.id, document.filename)
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


@router.post("/{document_id}/process", response_model=ProcessingResultOut)
async def process_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
    extraction_pipeline: ExtractionPipeline = Depends(get_extraction_pipeline),
    field_extraction_pipeline: FieldExtractionPipeline = Depends(get_field_extraction_pipeline),
    phi_validator: ValidationPipeline = Depends(get_phi_validator),
    validation_pipeline: ValidationPipeline = Depends(get_validation_pipeline),
) -> ProcessingResultOut:
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    await ingestion_service.update_status(db, document, DocumentStatus.PROCESSING)

    # Both calls below can take real wall-clock time (a large file read, and
    # especially OCR: rasterizing + recognizing every page of a PDF) and
    # are synchronous — calling them directly here would block the entire
    # event loop, stalling every other concurrent request (including
    # unrelated /health checks) for as long as this one document takes.
    # Confirmed directly: a 25-page PDF blocked a concurrent /health call
    # for the full ~20s OCR took. run_in_threadpool moves the blocking work
    # off the event loop thread.
    data = await run_in_threadpool(storage.read, document.storage_key)
    try:
        extraction_output = await run_in_threadpool(
            extraction_pipeline.extract, data=data, content_type=document.content_type
        )
    except ExtractionError as exc:
        # The pipeline couldn't read the bytes at all (corrupted file,
        # content-type/actual-content mismatch) — fail the document
        # cleanly instead of leaving it stuck in PROCESSING behind an
        # unhandled 500.
        extraction = ExtractionResult(
            document_id=document.id,
            raw_text=f"[EXTRACTION FAILED: {exc}]",
            fields={},
            confidence=0.0,
        )
        db.add(extraction)
        validation = ValidationResult(
            document_id=document.id,
            is_valid=False,
            issues=[f"extraction failed: {exc}"],
        )
        db.add(validation)
        await db.commit()
        await db.refresh(extraction)
        await db.refresh(validation)
        document = await ingestion_service.update_status(db, document, DocumentStatus.FAILED)

        logger.warning(
            "document processing failed: extraction error id=%s error=%s", document.id, exc
        )

        return ProcessingResultOut(
            document=DocumentOut.model_validate(document),
            extraction=ExtractionResultOut.model_validate(extraction),
            validation=ValidationResultOut.model_validate(validation),
        )

    # PHI-check the raw OCR text BEFORE calling the field-extraction LLM at
    # all: sending PHI-shaped content to a third-party API is a bigger
    # trust-boundary commitment than merely persisting it, so the gate has
    # to run first, not after. This also skips the LLM call entirely (no
    # cost, no external send) whenever it would have been discarded anyway.
    # Bare PHIDetectionValidator (not the full composite): fields don't
    # exist yet at this point, so RequiredFieldsValidator has nothing
    # meaningful to check.
    phi_precheck = phi_validator.validate(ExtractionOutput(raw_text=extraction_output.raw_text))

    if not phi_precheck.is_valid:
        extraction = ExtractionResult(
            document_id=document.id,
            raw_text=(
                f"[REDACTED: PHI detected in {len(extraction_output.raw_text)} "
                "characters of extracted text; not persisted]"
            ),
            fields={},
            confidence=extraction_output.confidence,
        )
        db.add(extraction)
        validation = ValidationResult(
            document_id=document.id,
            is_valid=False,
            issues=phi_precheck.issues,
        )
        db.add(validation)
        await db.commit()
        await db.refresh(extraction)
        await db.refresh(validation)
        document = await ingestion_service.update_status(db, document, DocumentStatus.FAILED)

        logger.warning(
            "document processing refused: PHI detected id=%s issues=%s",
            document.id,
            phi_precheck.issues,
        )

        return ProcessingResultOut(
            document=DocumentOut.model_validate(document),
            extraction=ExtractionResultOut.model_validate(extraction),
            validation=ValidationResultOut.model_validate(validation),
        )

    # raw_text is confirmed PHI-clean — safe to send to the field-extraction
    # LLM and to persist regardless of what happens next.
    try:
        field_output = await run_in_threadpool(
            field_extraction_pipeline.extract_fields, raw_text=extraction_output.raw_text
        )
    except FieldExtractionError as exc:
        extraction = ExtractionResult(
            document_id=document.id,
            raw_text=extraction_output.raw_text,
            fields={},
            confidence=0.0,
        )
        db.add(extraction)
        validation = ValidationResult(
            document_id=document.id,
            is_valid=False,
            issues=[f"field extraction failed: {exc}"],
        )
        db.add(validation)
        await db.commit()
        await db.refresh(extraction)
        await db.refresh(validation)
        document = await ingestion_service.update_status(db, document, DocumentStatus.FAILED)

        logger.warning(
            "document processing failed: field extraction error id=%s error=%s",
            document.id,
            exc,
        )

        return ProcessingResultOut(
            document=DocumentOut.model_validate(document),
            extraction=ExtractionResultOut.model_validate(extraction),
            validation=ValidationResultOut.model_validate(validation),
        )

    combined_output = ExtractionOutput(
        raw_text=extraction_output.raw_text,
        fields=field_output.fields,
        # Simple average of the OCR stage's confidence and the field
        # extraction stage's confidence — both are 0.0-1.0 measures of how
        # much to trust this ExtractionResult, and there's only one
        # confidence column to report it in.
        confidence=(extraction_output.confidence + field_output.confidence) / 2,
    )
    # Now runs the full composite (RequiredFieldsValidator + PHI) against
    # the real extracted fields. The PHI re-check is redundant (raw_text
    # was already confirmed clean above) but harmless; RequiredFieldsValidator
    # is now meaningful for the first time — the LLM can genuinely fail to
    # find a field, unlike the old synthetic fields, which always populated
    # all three.
    validation_output = validation_pipeline.validate(combined_output)

    extraction = ExtractionResult(
        document_id=document.id,
        raw_text=combined_output.raw_text,
        fields=combined_output.fields,
        confidence=combined_output.confidence,
    )
    db.add(extraction)
    await ingestion_service.update_status(db, document, DocumentStatus.EXTRACTED)

    validation = ValidationResult(
        document_id=document.id,
        is_valid=validation_output.is_valid,
        issues=validation_output.issues,
    )
    db.add(validation)
    await db.commit()
    await db.refresh(extraction)
    await db.refresh(validation)

    final_status = DocumentStatus.VALIDATED if validation_output.is_valid else DocumentStatus.FAILED
    document = await ingestion_service.update_status(db, document, final_status)

    logger.info(
        "document processed id=%s status=%s is_valid=%s",
        document.id,
        document.status,
        validation_output.is_valid,
    )

    return ProcessingResultOut(
        document=DocumentOut.model_validate(document),
        extraction=ExtractionResultOut.model_validate(extraction),
        validation=ValidationResultOut.model_validate(validation),
    )


@router.get("/{document_id}/result", response_model=ProcessingResultOut)
async def get_processing_result(
    document_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ProcessingResultOut:
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

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
    if extraction is None or validation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="document has not been processed yet",
        )

    return ProcessingResultOut(
        document=DocumentOut.model_validate(document),
        extraction=ExtractionResultOut.model_validate(extraction),
        validation=ValidationResultOut.model_validate(validation),
    )
