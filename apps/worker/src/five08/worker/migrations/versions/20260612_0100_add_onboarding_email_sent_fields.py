"""Add local onboarding email sent markers to people."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260612_0100"
down_revision = "20260611_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Track onboarding email sends in the local people cache."""
    op.add_column(
        "people",
        sa.Column(
            "onboarding_email_sent_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "people",
        sa.Column("onboarding_email_sent_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "people",
        sa.Column("onboarding_email_recipient", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_people_onboarding_email_sent_at",
        "people",
        ["onboarding_email_sent_at"],
    )


def downgrade() -> None:
    """Remove local onboarding email sent markers."""
    op.drop_index("idx_people_onboarding_email_sent_at", table_name="people")
    op.drop_column("people", "onboarding_email_recipient")
    op.drop_column("people", "onboarding_email_sent_by")
    op.drop_column("people", "onboarding_email_sent_at")
