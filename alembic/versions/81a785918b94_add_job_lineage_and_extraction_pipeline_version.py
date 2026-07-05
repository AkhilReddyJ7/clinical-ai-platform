"""add job lineage (attempt_number/trigger/trigger_note) and extraction_results.pipeline_version

Revision ID: 81a785918b94
Revises: 600e12de61a0
Create Date: 2026-07-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "81a785918b94"
down_revision: Union[str, Sequence[str], None] = "600e12de61a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "jobs",
        sa.Column("attempt_number", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "trigger",
            sa.Enum(
                "INITIAL_SUBMISSION",
                "RESUBMIT_AFTER_FAILURE",
                "FORCED_REPROCESS",
                name="jobtrigger",
                native_enum=False,
                length=30,
            ),
            server_default="INITIAL_SUBMISSION",
            nullable=False,
        ),
    )
    op.add_column("jobs", sa.Column("trigger_note", sa.Text(), nullable=True))
    op.add_column(
        "extraction_results",
        sa.Column("pipeline_version", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("extraction_results", "pipeline_version")
    op.drop_column("jobs", "trigger_note")
    op.drop_column("jobs", "trigger")
    op.drop_column("jobs", "attempt_number")
