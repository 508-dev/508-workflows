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
    op.create_index(
        "idx_people_onboarding_state",
        "people",
        ["onboarding_state", "onboarding_updated_at"],
    )


def downgrade() -> None:
    """Remove CRM onboarding state fields."""
    op.drop_index("idx_people_onboarding_state", table_name="people")
    op.drop_column("people", "onboarding_updated_at")
    op.drop_column("people", "onboarder")
    op.drop_column("people", "onboarding_state")
