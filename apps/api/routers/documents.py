import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_extraction_pipeline, get_storage, get_validation_pipeline
from apps.api.schemas import DocumentListOut, ProcessingResultOut
from modules.ingestion import service as ingestion_service
from modules.ingestion.models import DocumentStatus
from modules.ingestion.schemas import DocumentOut
from modules.ingestion.storage import StorageBackend
from modules.ocr.base import ExtractionPipeline
from modules.ocr.models import ExtractionResult
from modules.ocr.schemas import ExtractionResultOut
from modules.validation.base import ValidationPipeline
from modules.validation.models import ValidationResult
from modules.validation.schemas import ValidationResultOut
from shared.config.settings import get_settings
from shared.database.session import get_db
from shared.logging.logger import logger

settings = get_settings()
router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg", "text/plain"}


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

    data = await file.read()
    if len(data) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="file exceeds maximum upload size",
        )
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
    validation_pipeline: ValidationPipeline = Depends(get_validation_pipeline),
) -> ProcessingResultOut:
    document = await ingestion_service.get_document(db, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")

    await ingestion_service.update_status(db, document, DocumentStatus.PROCESSING)

    data = storage.read(document.storage_key)
    extraction_output = extraction_pipeline.extract(data=data, content_type=document.content_type)

    extraction = ExtractionResult(
        document_id=document.id,
        raw_text=extraction_output.raw_text,
        fields=extraction_output.fields,
        confidence=extraction_output.confidence,
    )
    db.add(extraction)
    await ingestion_service.update_status(db, document, DocumentStatus.EXTRACTED)

    validation_output = validation_pipeline.validate(extraction_output)
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
