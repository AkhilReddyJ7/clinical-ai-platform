"""add audit_log_entries table

Revision ID: 600e12de61a0
Revises: 6f19d9b702ee
Create Date: 2026-07-04 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "600e12de61a0"
down_revision: Union[str, Sequence[str], None] = "6f19d9b702ee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_log_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("caller", sa.String(length=255), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "DOCUMENT_UPLOADED",
                "JOB_ENQUEUED",
                name="auditaction",
                native_enum=False,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_log_entries_caller"), "audit_log_entries", ["caller"], unique=False
    )
    op.create_index(
        op.f("ix_audit_log_entries_document_id"), "audit_log_entries", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_audit_log_entries_job_id"), "audit_log_entries", ["job_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_audit_log_entries_job_id"), table_name="audit_log_entries")
    op.drop_index(op.f("ix_audit_log_entries_document_id"), table_name="audit_log_entries")
    op.drop_index(op.f("ix_audit_log_entries_caller"), table_name="audit_log_entries")
    op.drop_table("audit_log_entries")
