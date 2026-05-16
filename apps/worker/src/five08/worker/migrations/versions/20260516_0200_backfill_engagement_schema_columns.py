"""Backfill engagement columns added after early local migration runs."""

from __future__ import annotations

from alembic import op

revision = "20260516_0200"
down_revision = "20260516_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make existing databases match the current engagement schema."""
    op.execute(
        """
        ALTER TABLE job_post_channels
        ADD COLUMN IF NOT EXISTS posting_type text NOT NULL DEFAULT 'part_time'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_job_post_channels_posting_type'
            ) THEN
                ALTER TABLE job_post_channels
                ADD CONSTRAINT ck_job_post_channels_posting_type
                CHECK (
                    posting_type IN (
                        'part_time',
                        'full_time',
                        'part_time_or_full_time',
                        'unknown'
                    )
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        ADD COLUMN IF NOT EXISTS discord_channel_name text
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        ADD COLUMN IF NOT EXISTS posting_type text NOT NULL DEFAULT 'part_time'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_engagements_posting_type'
            ) THEN
                ALTER TABLE engagements
                ADD CONSTRAINT ck_engagements_posting_type
                CHECK (
                    posting_type IN (
                        'part_time',
                        'full_time',
                        'part_time_or_full_time',
                        'unknown'
                    )
                );
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        ADD COLUMN IF NOT EXISTS last_recruiting_reminder_at timestamptz
        """
    )


def downgrade() -> None:
    """No-op: these columns are now part of the base engagement schema."""
