"""Create discord member cache table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260304_0200"
down_revision = "20260304_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create discord_members table with updated_at trigger."""
    op.create_table(
        "discord_members",
        sa.Column("discord_user_id", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("discord_username", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column(
            "roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.PrimaryKeyConstraint(
            "guild_id",
            "discord_user_id",
            name="pk_discord_members",
        ),
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

    op.create_index("idx_discord_members_guild_id", "discord_members", ["guild_id"])

    op.execute(
        """
        CREATE FUNCTION discord_members_set_updated_at_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER discord_members_set_updated_at_tr
        BEFORE UPDATE ON discord_members
        FOR EACH ROW
        EXECUTE FUNCTION discord_members_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop discord_members table."""
    op.execute(
        "DROP TRIGGER IF EXISTS discord_members_set_updated_at_tr ON discord_members"
    )
    op.execute("DROP FUNCTION IF EXISTS discord_members_set_updated_at_fn()")

    op.drop_index("idx_discord_members_guild_id", table_name="discord_members")
    op.drop_table("discord_members")
