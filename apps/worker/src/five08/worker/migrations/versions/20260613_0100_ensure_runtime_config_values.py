"""Ensure runtime configuration table exists."""

from __future__ import annotations

from alembic import op

revision = "20260613_0100"
down_revision = "20260612_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair databases stamped past the runtime config migration."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_config_values (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_by_provider TEXT,
            updated_by_subject TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        )
        """
    )


def downgrade() -> None:
    """Keep runtime configuration storage when rolling back this repair."""
