"""Add onboarding volunteer, reminder, and candidate-role persistence."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_0100"
down_revision = "20260819_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist volunteer availability and idempotent onboarding reminders."""
    op.add_column(
        "people",
        sa.Column(
            "professional_roles",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_table(
        "onboarding_volunteers",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.Text(), nullable=False, unique=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column(
            "availability",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'available'"),
        ),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_active_assignments", sa.Integer(), nullable=True),
        sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'paused')",
            name="ck_onboarding_volunteers_availability",
        ),
        sa.CheckConstraint(
            "max_active_assignments IS NULL OR max_active_assignments > 0",
            name="ck_onboarding_volunteers_max_active_assignments",
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_onboarding_volunteers_availability",
        "onboarding_volunteers",
        ["availability", "paused_until", "last_assigned_at"],
    )
    op.create_table(
        "onboarding_reminder_deliveries",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reminder_number", sa.Integer(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("person_id", "stage", "activity_at", "reminder_number"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_onboarding_reminder_deliveries_due",
        "onboarding_reminder_deliveries",
        ["sent_at", "claimed_at"],
    )


def downgrade() -> None:
    """Remove onboarding volunteer, reminder, and candidate-role persistence."""
    op.drop_index(
        "idx_onboarding_reminder_deliveries_due",
        table_name="onboarding_reminder_deliveries",
    )
    op.drop_table("onboarding_reminder_deliveries")
    op.drop_index(
        "idx_onboarding_volunteers_availability", table_name="onboarding_volunteers"
    )
    op.drop_table("onboarding_volunteers")
    op.drop_column("people", "professional_roles")
