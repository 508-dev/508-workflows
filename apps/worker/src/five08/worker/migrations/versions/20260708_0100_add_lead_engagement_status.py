"""Add lead gig status."""

from __future__ import annotations

from alembic import op

revision = "20260708_0100"
down_revision = "20260706_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Allow sourced job leads to be posted before they are qualified."""
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


def downgrade() -> None:
    """Restore the previous gig status constraint."""
    op.execute(
        """
        UPDATE engagements
        SET status = 'unknown'
        WHERE status = 'lead'
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
                'recruiting',
                'filled',
                'unknown',
                'lost',
                'outdated'
            )
        )
        """
    )
