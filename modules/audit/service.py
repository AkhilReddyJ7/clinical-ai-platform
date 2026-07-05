import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.audit.models import AuditAction, AuditLogEntry
from shared.logging.logger import logger


async def record_action(
    db: AsyncSession,
    *,
    caller: str,
    action: AuditAction,
    document_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
) -> AuditLogEntry | None:
    """Writes one audit entry (ADR-0027). Never raises: a failure here is
    logged and the write is rolled back, but never propagated to the
    caller -- the action being audited must not fail because the audit
    write did (the same "observability must never break execution"
    principle modules/processing/events.py already establishes for
    metrics/logging). Returns None on failure so a caller that wants to
    notice (tests, mainly) can, without being forced to handle an
    exception on the common path.
    """
    entry = AuditLogEntry(caller=caller, action=action, document_id=document_id, job_id=job_id)
    try:
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
    except Exception:
        await db.rollback()
        logger.exception(
            "audit: failed to record action=%s caller=%s document_id=%s job_id=%s",
            action.value,
            caller,
            document_id,
            job_id,
        )
        return None
    return entry


async def list_entries(
    db: AsyncSession,
    *,
    caller: str | None = None,
    action: AuditAction | None = None,
    document_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[AuditLogEntry], int]:
    """GET /audit's query (ADR-0028): every filter is optional and
    combinable, each mapping directly to an already-indexed column
    (ADR-0027) -- no new query path. Global, not caller-scoped: any
    authenticated caller may query any other caller's entries (ADR-0028
    section 2), consistent with every other resource in this project
    having identical access regardless of who's asking.
    """
    filters = []
    if caller is not None:
        filters.append(AuditLogEntry.caller == caller)
    if action is not None:
        filters.append(AuditLogEntry.action == action)
    if document_id is not None:
        filters.append(AuditLogEntry.document_id == document_id)
    if job_id is not None:
        filters.append(AuditLogEntry.job_id == job_id)

    total = await db.scalar(select(func.count()).select_from(AuditLogEntry).where(*filters))
    result = await db.execute(
        select(AuditLogEntry)
        .where(*filters)
        .order_by(AuditLogEntry.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all()), total or 0
