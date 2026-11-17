"""Fence reclaimed agent schedule executions with durable owner tokens."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260804_0200"
down_revision = "20260804_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add one token per active schedule-run execution lease."""

    op.add_column(
        "agent_schedule_runs",
        sa.Column("execution_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Existing active runs predate token-aware callers.  Their unique run ID
    # provides a stable legacy owner until they finish or are reclaimed, at
    # which point the application writes a fresh random token.
    op.execute(
        """
        UPDATE agent_schedule_runs
        SET execution_token = id
        WHERE status = 'running'
          AND execution_token IS NULL
        """
    )


def downgrade() -> None:
    """Remove the schedule-run execution fence."""

    op.drop_column("agent_schedule_runs", "execution_token")
