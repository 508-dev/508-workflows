"""Add unavailable gig application status."""

from __future__ import annotations

from alembic import op

revision = "20260601_0100"
down_revision = "20260519_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow dashboard users to mark gig candidates unavailable."""
    op.execute(
        """
        ALTER TABLE engagement_applications
        DROP CONSTRAINT IF EXISTS ck_engagement_applications_status
        """
    )
    op.execute(
        """
        ALTER TABLE engagement_applications
        ADD CONSTRAINT ck_engagement_applications_status
        CHECK (
            status IN (
                'suggested',
                'interested',
                'reviewing',
                'contacted',
                'accepted',
                'unavailable',
                'rejected',
                'withdrawn'
            )
        )
        """
    )


def downgrade() -> None:
    """Restore the previous gig application status constraint."""
    op.execute(
        """
        UPDATE engagement_applications
        SET status = 'withdrawn'
        WHERE status = 'unavailable'
        """
    )
    op.execute(
        """
        ALTER TABLE engagement_applications
        DROP CONSTRAINT IF EXISTS ck_engagement_applications_status
        """
    )
    op.execute(
        """
        ALTER TABLE engagement_applications
        ADD CONSTRAINT ck_engagement_applications_status
        CHECK (
            status IN (
                'suggested',
                'interested',
                'reviewing',
                'contacted',
                'accepted',
                'rejected',
                'withdrawn'
            )
        )
        """
    )
