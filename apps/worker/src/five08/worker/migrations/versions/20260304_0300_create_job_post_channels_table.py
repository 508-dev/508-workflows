"""Create job_post_channels table for Discord job matching automation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260304_0300"
down_revision = "20260304_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create guild/channel registration table for automatic job matching."""
    op.create_table(
        "job_post_channels",
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint(
            "guild_id",
            "channel_id",
            name="pk_job_post_channels",
        ),
    )

    op.create_index(
        "idx_job_post_channels_guild_id",
        "job_post_channels",
        ["guild_id"],
    )


def downgrade() -> None:
    """Drop job_post_channels table."""
    op.drop_index("idx_job_post_channels_guild_id", table_name="job_post_channels")
    op.drop_table("job_post_channels")
