import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from modules.audit.models import AuditAction


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    caller: str
    action: AuditAction
    document_id: uuid.UUID | None
    job_id: uuid.UUID | None
    created_at: datetime
