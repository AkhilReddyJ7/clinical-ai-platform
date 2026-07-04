import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.schemas import AuditLogListOut
from modules.audit import service as audit_service
from modules.audit.models import AuditAction
from modules.audit.schemas import AuditLogEntryOut
from modules.auth.api_key import require_api_key
from shared.database.session import get_db

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=AuditLogListOut)
async def list_audit_entries(
    caller: str | None = None,
    action: AuditAction | None = None,
    document_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListOut:
    """GET /audit (ADR-0028): global, filterable, paginated read over
    every caller's audit history -- no per-caller visibility restriction
    (ADR-0028 section 2), consistent with ADR-0026's flat access model.
    """
    entries, total = await audit_service.list_entries(
        db,
        caller=caller,
        action=action,
        document_id=document_id,
        job_id=job_id,
        limit=limit,
        offset=offset,
    )
    return AuditLogListOut(
        items=[AuditLogEntryOut.model_validate(entry) for entry in entries],
        total=total,
        limit=limit,
        offset=offset,
    )
