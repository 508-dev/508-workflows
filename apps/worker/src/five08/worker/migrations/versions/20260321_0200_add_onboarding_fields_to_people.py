"""Add onboarding queue fields to the people table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260321_0200"
down_revision = "20260321_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add CRM onboarding state fields for dashboard queue views."""
    op.add_column("people", sa.Column("onboarding_state", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("onboarder", sa.Text(), nullable=True))
    op.add_column(
        "people",
        sa.Column("onboarding_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        # Match the dashboard's normalized onboarding_state filter expression.
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_people_onboarding_state
            ON people (
                (replace(
                    replace(
                        replace(lower(btrim(onboarding_state)), '_', ''),
                        '-',
                        ''
                    ),
                    ' ',
                    ''
                )),
                onboarding_updated_at
            )
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_people_dashboard_search_trgm
            ON people USING gin (
                (concat_ws(
                    ' ',
                    crm_contact_id,
                    name,
                    email,
                    email_508,
                    discord_user_id,
                    discord_username,
                    github_username,
                    contact_type,
                    address_country,
                    address_city,
                    address_state,
                    seniority,
                    latest_resume_name
                )) gin_trgm_ops
            )
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_people_onboarding_search_trgm
            ON people USING gin (
                (concat_ws(
                    ' ',
                    name,
                    email,
                    email_508,
                    discord_user_id,
                    discord_username,
                    onboarder,
                    onboarding_state
                )) gin_trgm_ops
            )
            """
        )


def downgrade() -> None:
    """Remove CRM onboarding state fields."""
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS idx_people_onboarding_search_trgm"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_people_dashboard_search_trgm")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_people_onboarding_state")
    op.drop_column("people", "onboarding_updated_at")
    op.drop_column("people", "onboarder")
    op.drop_column("people", "onboarding_state")
