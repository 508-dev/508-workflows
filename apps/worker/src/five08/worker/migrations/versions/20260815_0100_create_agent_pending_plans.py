"""Persist pending agent confirmations across API replicas and restarts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260815_0100"
down_revision = "20260804_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store unconsumed, user-owned frozen plans until confirmation or expiry."""

    op.create_table(
        "agent_pending_plans",
        sa.Column("plan_id", sa.Text(), nullable=False, primary_key=True),
        sa.Column("owner_discord_user_id", sa.Text(), nullable=False),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "original_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(plan_id)) BETWEEN 1 AND 128",
            name="ck_agent_pending_plans_plan_id_length",
        ),
        sa.CheckConstraint(
            "char_length(btrim(owner_discord_user_id)) BETWEEN 1 AND 128",
            name="ck_agent_pending_plans_owner_discord_user_id_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(plan) = 'object'",
            name="ck_agent_pending_plans_plan_object",
        ),
        sa.CheckConstraint(
            "octet_length(plan::text) <= 65536",
            name="ck_agent_pending_plans_plan_size",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(original_context) = 'object'",
            name="ck_agent_pending_plans_original_context_object",
        ),
        sa.CheckConstraint(
            "octet_length(original_context::text) <= 32768",
            name="ck_agent_pending_plans_original_context_size",
        ),
    )
    op.create_index(
        "idx_agent_pending_plans_expires_at",
        "agent_pending_plans",
        ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )
    op.create_index(
        "idx_agent_pending_plans_owner_discord_user_id",
        "agent_pending_plans",
        ["owner_discord_user_id"],
    )


def downgrade() -> None:
    """Remove persisted pending confirmation plans."""

    op.drop_index(
        "idx_agent_pending_plans_owner_discord_user_id",
        table_name="agent_pending_plans",
    )
    op.drop_index(
        "idx_agent_pending_plans_expires_at",
        table_name="agent_pending_plans",
    )
    op.drop_table("agent_pending_plans")
