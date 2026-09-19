"""Persist short-lived agent clarification and confirmation state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260917_0100"
down_revision = "20260916_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_states",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("reference_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('plan', 'clarification')", name="ck_agent_states_kind"
        ),
    )
    op.create_index("idx_agent_states_expiry", "agent_states", ["expires_at"])
    op.create_index("idx_agent_states_reference", "agent_states", ["reference_id"])


def downgrade() -> None:
    op.drop_table("agent_states")
