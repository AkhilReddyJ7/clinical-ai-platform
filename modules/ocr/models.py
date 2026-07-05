import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ExtractionResult(Base):
    __tablename__ = "extraction_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    # Which job attempt produced this result. Nullable: existing rows (and
    # any future direct/synchronous write) predate a job existing at all.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    raw_text: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Identifies the field-extraction backend/model that produced this
    # result (ADR-0031) -- e.g. "anthropic:claude-haiku-4-5" or "mock".
    # Scoped to field extraction only: the component with a real
    # versioning axis in practice today. Nullable: pre-existing rows
    # predate this column.
    pipeline_version: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
