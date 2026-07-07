"""Create sourced job leads review table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260706_0100"
down_revision = "20260614_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create external job leads table with explicit review states."""
    op.create_table(
        "job_leads",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("external_parent_id", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization", sa.Text(), nullable=True),
        sa.Column("body_raw", sa.Text(), nullable=False),
        sa.Column("body_normalized", sa.Text(), nullable=False),
        sa.Column(
            "posting_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'part_time'"),
        ),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("remote", sa.Boolean(), nullable=True),
        sa.Column("apply_url", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("reviewed_by_discord_user_id", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discord_guild_id", sa.Text(), nullable=True),
        sa.Column("discord_channel_id", sa.Text(), nullable=True),
        sa.Column("discord_thread_id", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'approved', 'rejected', 'posted')",
            name="ck_job_leads_status",
        ),
        sa.CheckConstraint(
            "posting_type IN ('part_time', 'full_time', 'part_time_or_full_time', 'unknown')",
            name="ck_job_leads_posting_type",
        ),
        sa.UniqueConstraint(
            "source_key",
            "external_id",
            name="uq_job_leads_source_external_id",
        ),
    )
    op.create_index("idx_job_leads_status", "job_leads", ["status"])
    op.create_index(
        "idx_job_leads_source_parent",
        "job_leads",
        ["source_key", "external_parent_id"],
    )
    op.create_index(
        "idx_job_leads_source_posted_at",
        "job_leads",
        ["source_posted_at"],
    )
    op.create_index(
        "idx_job_leads_tags",
        "job_leads",
        ["tags"],
        postgresql_using="gin",
    )

    op.execute(
        """
        CREATE TRIGGER job_leads_set_updated_at
        BEFORE UPDATE ON job_leads
        FOR EACH ROW EXECUTE FUNCTION engagements_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop external job leads table."""
    op.execute("DROP TRIGGER IF EXISTS job_leads_set_updated_at ON job_leads")
    op.drop_index("idx_job_leads_tags", table_name="job_leads")
    op.drop_index("idx_job_leads_source_posted_at", table_name="job_leads")
    op.drop_index("idx_job_leads_source_parent", table_name="job_leads")
    op.drop_index("idx_job_leads_status", table_name="job_leads")
    op.drop_table("job_leads")
