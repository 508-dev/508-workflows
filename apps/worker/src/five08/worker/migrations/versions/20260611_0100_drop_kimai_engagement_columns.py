"""Drop unused Kimai engagement columns."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260611_0100"
down_revision = "20260610_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove legacy time-tracking identifiers from engagements."""
    op.drop_column("engagements", "kimai_customer_id")
    op.drop_column("engagements", "kimai_project_id")


def downgrade() -> None:
    """Restore legacy time-tracking identifier columns."""
    op.add_column(
        "engagements",
        sa.Column("kimai_project_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "engagements",
        sa.Column("kimai_customer_id", sa.Text(), nullable=True),
    )
