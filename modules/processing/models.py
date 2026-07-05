import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.base import Base


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobTrigger(str, enum.Enum):
    """Why this Job row exists (ADR-0031/0032) -- not to be confused with
    JobStatus.RETRYING, which is an internal transient retry of the same
    job row, not a new one. A new Job row (and therefore a new trigger)
    is only ever created on resubmit.
    """

    INITIAL_SUBMISSION = "initial_submission"
    RESUBMIT_AFTER_FAILURE = "resubmit_after_failure"
    FORCED_REPROCESS = "forced_reprocess"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    """One processing-attempt series against a document (ADR-0020).

    A job represents a single series of attempts, not a single attempt:
    internal retries move the same job between ``running`` and
    ``retrying`` rather than creating new rows. A new job row is only
    created when a document is resubmitted for processing.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        # Supports the claim query's `WHERE status = 'queued' ORDER BY
        # created_at` without a full table scan as the queue grows.
        Index("ix_jobs_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20),
        default=JobStatus.QUEUED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Consumed by both an ordinary transient-failure retry and a stale-job
    # reclaim (ADR-0023, ADR-0024) — a count of attempts, not a record of
    # why each one happened.
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The most recent terminal failure's message. Last-known-error only,
    # not a full attempt history — that's the audit trail's job, per
    # ADR-0023 section 6, not this column's.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Set when a job enters `retrying`: the earliest time the backoff-driven
    # reclaim (ADR-0023 section 3) may pick this job back up as `running`.
    # None otherwise (queued/running/terminal jobs have no pending attempt).
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # 1-indexed count of this document's Job rows, computed at creation
    # time (repository.py) under the same document-row lock enqueue_job/
    # force_reprocess_job already take (ADR-0031) -- the lineage ordinal,
    # not a Job-table-wide sequence. Pre-existing rows backfill to 1
    # regardless of true history (see the migration).
    attempt_number: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    trigger: Mapped[JobTrigger] = mapped_column(
        Enum(JobTrigger, native_enum=False, length=30),
        default=JobTrigger.INITIAL_SUBMISSION,
    )
    # Operator-supplied justification for a forced reprocess (ADR-0032).
    # Always None for INITIAL_SUBMISSION/RESUBMIT_AFTER_FAILURE.
    trigger_note: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
