"""Add Discord holding-thread metadata to sourced job leads."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_0100"
down_revision = "20260711_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store unqualified-lead staging threads separately from promoted gigs."""
    op.add_column(
        "job_leads",
        sa.Column("staged_discord_guild_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_leads",
        sa.Column("staged_discord_channel_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_leads",
        sa.Column("staged_discord_thread_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "job_leads",
        sa.Column("staged_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Remove sourced-lead holding-thread metadata."""
    op.drop_column("job_leads", "staged_at")
    op.drop_column("job_leads", "staged_discord_thread_id")
    op.drop_column("job_leads", "staged_discord_channel_id")
    op.drop_column("job_leads", "staged_discord_guild_id")
