"""Create jobs table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202602210100"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the job persistence table and supporting indexes."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default=sa.text("8")
        ),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "status IN ('queued', 'running', 'succeeded', 'failed', 'dead', 'canceled')",
            name="ck_jobs_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_jobs_idempotency_key",
        ),
    )
    op.create_index("idx_jobs_status_run_after", "jobs", ["status", "run_after"])
    op.create_index("idx_jobs_created_at", "jobs", ["created_at"])
    op.execute(
        """
        CREATE FUNCTION jobs_set_updated_at_fn()
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
        CREATE TRIGGER jobs_set_updated_at_tr
        BEFORE UPDATE ON jobs
        FOR EACH ROW
        EXECUTE FUNCTION jobs_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop the job persistence table."""
    op.execute("DROP TRIGGER IF EXISTS jobs_set_updated_at_tr ON jobs")
    op.execute("DROP FUNCTION IF EXISTS jobs_set_updated_at_fn()")
    op.drop_index("idx_jobs_created_at", table_name="jobs")
    op.drop_index("idx_jobs_status_run_after", table_name="jobs")
    op.drop_table("jobs")
