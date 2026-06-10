"""Create runtime configuration table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260610_0100"
down_revision = "20260601_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create admin-managed runtime configuration storage."""
    op.create_table(
        "runtime_config_values",
        sa.Column("key", sa.Text(), nullable=False, primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by_provider", sa.Text(), nullable=True),
        sa.Column("updated_by_subject", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    """Drop admin-managed runtime configuration storage."""
    op.drop_table("runtime_config_values")
