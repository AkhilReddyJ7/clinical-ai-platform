import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from modules.processing.models import JobStatus, JobTrigger


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    status: JobStatus
    attempt_number: int
    trigger: JobTrigger
    trigger_note: str | None
    created_at: datetime
