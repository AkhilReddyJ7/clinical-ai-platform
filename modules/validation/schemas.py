import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ValidationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    is_valid: bool
    issues: list[str]
    created_at: datetime
