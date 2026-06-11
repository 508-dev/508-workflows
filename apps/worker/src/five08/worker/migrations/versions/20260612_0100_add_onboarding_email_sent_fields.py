"""Add local onboarding email sent markers to people."""

from __future__ import annotations

from alembic import op

revision = "20260612_0100"
down_revision = "20260611_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Track onboarding email sends in the local people cache."""
    op.execute(
        """
        ALTER TABLE people
        ADD COLUMN IF NOT EXISTS onboarding_email_sent_at TIMESTAMP WITH TIME ZONE
        """
    )
    op.execute(
        """
        ALTER TABLE people
        ADD COLUMN IF NOT EXISTS onboarding_email_sent_by TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE people
        ADD COLUMN IF NOT EXISTS onboarding_email_recipient TEXT
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_people_onboarding_email_sent_at
        ON people (onboarding_email_sent_at)
        """
    )


def downgrade() -> None:
    """Remove local onboarding email sent markers."""
    op.execute("DROP INDEX IF EXISTS idx_people_onboarding_email_sent_at")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS onboarding_email_recipient")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS onboarding_email_sent_by")
    op.execute("ALTER TABLE people DROP COLUMN IF EXISTS onboarding_email_sent_at")
