"""add jobs next_attempt_at column

Revision ID: 6f19d9b702ee
Revises: b05e4fbbd0ca
Create Date: 2026-07-04 10:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "6f19d9b702ee"
down_revision: Union[str, Sequence[str], None] = "b05e4fbbd0ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("jobs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "next_attempt_at")
