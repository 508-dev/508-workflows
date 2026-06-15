"""Create durable onboarding intake submissions table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260613_0300"
down_revision = "20260613_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store raw and normalized third-party form intake submissions."""
    op.create_table(
        "onboarding_intake_submissions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("form_id", sa.Text(), nullable=True),
        sa.Column("submission_id", sa.Text(), nullable=True),
        sa.Column("crm_contact_id", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "normalized_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
            "source IN ('google_forms', 'tally')",
            name="ck_onboarding_intake_submissions_source",
        ),
    )
    op.create_index(
        "uq_onboarding_intake_submissions_source_form_submission",
        "onboarding_intake_submissions",
        [
            "source",
            sa.text("COALESCE(form_id, '')"),
            sa.text("COALESCE(submission_id, '')"),
        ],
        unique=True,
    )
    op.create_index(
        "idx_onboarding_intake_submissions_contact_created",
        "onboarding_intake_submissions",
        ["crm_contact_id", "created_at"],
    )
    op.create_index(
        "idx_onboarding_intake_submissions_email_created",
        "onboarding_intake_submissions",
        ["email", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION onboarding_intake_submissions_set_updated_at_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER onboarding_intake_submissions_set_updated_at_tr
        BEFORE UPDATE ON onboarding_intake_submissions
        FOR EACH ROW
        EXECUTE FUNCTION onboarding_intake_submissions_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop durable onboarding intake submissions table."""
    op.execute(
        "DROP TRIGGER IF EXISTS onboarding_intake_submissions_set_updated_at_tr "
        "ON onboarding_intake_submissions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS onboarding_intake_submissions_set_updated_at_fn()"
    )
    op.drop_index(
        "idx_onboarding_intake_submissions_email_created",
        table_name="onboarding_intake_submissions",
    )
    op.drop_index(
        "idx_onboarding_intake_submissions_contact_created",
        table_name="onboarding_intake_submissions",
    )
    op.drop_index(
        "uq_onboarding_intake_submissions_source_form_submission",
        table_name="onboarding_intake_submissions",
    )
    op.drop_table("onboarding_intake_submissions")
