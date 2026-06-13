"""Add trigram indexes for dashboard gig search."""

from __future__ import annotations

from alembic import op

revision = "20260613_0200"
down_revision = "20260613_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Index the leading-wildcard expressions used by dashboard gig search."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_engagements_dashboard_search_trgm"
        )
        op.execute(
            """
            DROP INDEX CONCURRENTLY IF EXISTS
                idx_engagement_applications_dashboard_search_trgm
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_engagements_dashboard_search_trgm
            ON engagements USING gin (
                (
                    coalesce(title, '') || ' ' ||
                    coalesce(body_raw, '') || ' ' ||
                    coalesce(body_normalized, '')
                ) gin_trgm_ops
            )
            """
        )


def downgrade() -> None:
    """Remove dashboard gig search trigram indexes."""
    with op.get_context().autocommit_block():
        op.execute(
            """
            DROP INDEX CONCURRENTLY IF EXISTS
                idx_engagement_applications_dashboard_search_trgm
            """
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_engagements_dashboard_search_trgm"
        )
