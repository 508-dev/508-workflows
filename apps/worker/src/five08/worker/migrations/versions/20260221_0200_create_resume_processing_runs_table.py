"""Create resume processing runs table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202602210200"
down_revision = "202602210100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create per-resume processing ledger table."""
    op.create_table(
        "resume_processing_runs",
        sa.Column("contact_id", sa.Text(), nullable=False),
        sa.Column("attachment_id", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
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
        sa.PrimaryKeyConstraint(
            "contact_id",
            "attachment_id",
            "extractor_version",
            "model_name",
            name="pk_resume_processing_runs",
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="ck_resume_processing_runs_status",
        ),
    )
    op.create_index(
        "idx_resume_processing_runs_processed_at",
        "resume_processing_runs",
        ["processed_at"],
    )
    op.execute(
        """
        CREATE FUNCTION resume_processing_runs_set_updated_at_fn()
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
        CREATE TRIGGER resume_processing_runs_set_updated_at_tr
        BEFORE UPDATE ON resume_processing_runs
        FOR EACH ROW
        EXECUTE FUNCTION resume_processing_runs_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop resume processing ledger table."""
    op.execute(
        "DROP TRIGGER IF EXISTS resume_processing_runs_set_updated_at_tr ON resume_processing_runs"
    )
    op.execute("DROP FUNCTION IF EXISTS resume_processing_runs_set_updated_at_fn()")
    op.drop_index(
        "idx_resume_processing_runs_processed_at",
        table_name="resume_processing_runs",
    )
    op.drop_table("resume_processing_runs")
