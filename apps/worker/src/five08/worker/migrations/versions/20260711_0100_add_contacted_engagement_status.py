"""Add contacted gig status and generalize gig status reminders."""

from __future__ import annotations

from alembic import op

revision = "20260711_0100"
down_revision = "20260709_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow contacted gigs and use one reminder timestamp for active statuses."""
    op.execute(
        """
        ALTER TABLE engagements
        RENAME COLUMN last_recruiting_reminder_at TO last_status_reminder_at
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        DROP CONSTRAINT IF EXISTS ck_engagements_status
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        ADD CONSTRAINT ck_engagements_status
        CHECK (
            status IN (
                'lead',
                'recruiting',
                'contacted',
                'filled',
                'unknown',
                'lost',
                'outdated',
                'duplicate'
            )
        )
        """
    )


def downgrade() -> None:
    """Remove contacted status and restore the previous reminder column name."""
    op.execute(
        """
        UPDATE engagements
        SET status = 'unknown'
        WHERE status = 'contacted'
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        DROP CONSTRAINT IF EXISTS ck_engagements_status
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        ADD CONSTRAINT ck_engagements_status
        CHECK (
            status IN (
                'lead',
                'recruiting',
                'filled',
                'unknown',
                'lost',
                'outdated',
                'duplicate'
            )
        )
        """
    )
    op.execute(
        """
        ALTER TABLE engagements
        RENAME COLUMN last_status_reminder_at TO last_recruiting_reminder_at
        """
    )
