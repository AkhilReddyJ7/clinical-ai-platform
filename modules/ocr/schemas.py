import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExtractionResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    raw_text: str
    fields: dict[str, str]
    confidence: float
    created_at: datetime
