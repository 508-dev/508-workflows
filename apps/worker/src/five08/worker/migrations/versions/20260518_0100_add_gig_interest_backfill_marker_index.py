"""Add unique gig interest backfill marker index."""

from __future__ import annotations

from alembic import op

revision = "20260518_0100"
down_revision = "20260516_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Ensure upgraded databases have the partial unique marker index."""
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_engagement_events_gig_interest_backfill_marker
        ON engagement_events (engagement_id, event_type)
        WHERE event_type = 'gig_thread_interest_backfilled'
        """
    )


def downgrade() -> None:
    """Remove the gig interest backfill marker index."""
    op.execute("DROP INDEX IF EXISTS uq_engagement_events_gig_interest_backfill_marker")
