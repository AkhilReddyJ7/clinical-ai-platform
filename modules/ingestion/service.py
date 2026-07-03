import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ingestion.models import Document, DocumentStatus
from modules.ingestion.storage import StorageBackend


async def register_document(
    db: AsyncSession,
    storage: StorageBackend,
    *,
    filename: str,
    content_type: str,
    data: bytes,
) -> Document:
    document_id = uuid.uuid4()
    storage_key = f"{document_id}/{filename}"
    storage.save(storage_key, data)

    document = Document(
        id=document_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=storage_key,
        status=DocumentStatus.UPLOADED,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def list_documents(db: AsyncSession) -> list[Document]:
    result = await db.execute(select(Document).order_by(Document.created_at.desc()))
    return list(result.scalars().all())


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> Document | None:
    return await db.get(Document, document_id)


async def update_status(db: AsyncSession, document: Document, status: DocumentStatus) -> Document:
    document.status = status
    await db.commit()
    await db.refresh(document)
    return document
