"""Add an idempotency key for recurring schedule creation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260923_0100"
down_revision = "20260920_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make retried create operations return one durable schedule."""

    op.add_column(
        "agent_schedules",
        sa.Column("creation_operation_id", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_agent_schedules_creation_operation_id_length",
        "agent_schedules",
        "creation_operation_id IS NULL OR "
        "char_length(btrim(creation_operation_id)) BETWEEN 1 AND 128",
    )
    op.create_index(
        "uq_agent_schedules_creation_operation",
        "agent_schedules",
        ["organization_id", "owner_discord_user_id", "creation_operation_id"],
        unique=True,
        postgresql_where=sa.text("creation_operation_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove schedule-creation idempotency metadata."""

    op.drop_index(
        "uq_agent_schedules_creation_operation",
        table_name="agent_schedules",
    )
    op.drop_constraint(
        "ck_agent_schedules_creation_operation_id_length",
        "agent_schedules",
        type_="check",
    )
    op.drop_column("agent_schedules", "creation_operation_id")
