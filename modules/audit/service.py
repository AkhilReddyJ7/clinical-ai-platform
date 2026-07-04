import uuid

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
