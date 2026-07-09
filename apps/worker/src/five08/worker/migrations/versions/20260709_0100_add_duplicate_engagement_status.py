"""Add duplicate gig status."""

from __future__ import annotations

from alembic import op

revision = "20260709_0100"
down_revision = "20260708_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow duplicate gig status for closed duplicate forum posts."""
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


def downgrade() -> None:
    """Remove duplicate gig status from the status constraint."""
    op.execute(
        """
        UPDATE engagements
        SET status = 'unknown'
        WHERE status = 'duplicate'
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
                'outdated'
            )
        )
        """
    )
