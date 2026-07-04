import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.base import Base


class AuditAction(str, enum.Enum):
    DOCUMENT_UPLOADED = "document_uploaded"
    JOB_ENQUEUED = "job_enqueued"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogEntry(Base):
    """One caller-attributed action: who did what, when (ADR-0027).

    Deliberately has no free-text column: document_id/job_id correlate
    with the Document/Job rows that already hold whatever content is
    appropriate to store, at whatever access-control/retention policy
    those tables already have. This table never duplicates it -- the
    absence of such a column is the redaction policy, not a filter
    someone has to remember to apply. Append-only: never updated (no
    updated_at) or deleted by application code.
    """

    __tablename__ = "audit_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    caller: Mapped[str] = mapped_column(String(255), index=True)
    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction, native_enum=False, length=30))
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
