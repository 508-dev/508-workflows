"""Add durable at-most-once report delivery state to agent schedule runs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260729_0100"
down_revision = "20260728_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Track the single Discord delivery attempt for each durable run."""

    op.add_column(
        "agent_schedule_runs",
        sa.Column(
            "delivery_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "agent_schedule_runs",
        sa.Column("delivery_message_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_schedule_runs",
        sa.Column("delivery_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_agent_schedule_runs_delivery_status",
        "agent_schedule_runs",
        "delivery_status IN ('pending', 'claimed', 'posted', 'unknown')",
    )


def downgrade() -> None:
    """Remove schedule report-delivery idempotency state."""

    op.drop_constraint(
        "ck_agent_schedule_runs_delivery_status",
        "agent_schedule_runs",
        type_="check",
    )
    op.drop_column("agent_schedule_runs", "delivery_claimed_at")
    op.drop_column("agent_schedule_runs", "delivery_message_id")
    op.drop_column("agent_schedule_runs", "delivery_status")
