"""Create engagement tables for Discord gig tracking."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260516_0100"
down_revision = "20260321_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create local engagement and application tracking tables."""
    op.add_column(
        "job_post_channels",
        sa.Column(
            "posting_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'part_time'"),
        ),
    )
    op.create_check_constraint(
        "ck_job_post_channels_posting_type",
        "job_post_channels",
        "posting_type IN ('part_time', 'full_time', 'part_time_or_full_time', 'unknown')",
    )

    op.create_table(
        "engagements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "lifecycle_stage",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending_gig'"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_raw", sa.Text(), nullable=True),
        sa.Column("body_normalized", sa.Text(), nullable=True),
        sa.Column(
            "required_skills",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "preferred_skills",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("discord_guild_id", sa.Text(), nullable=True),
        sa.Column("discord_channel_id", sa.Text(), nullable=True),
        sa.Column("discord_channel_name", sa.Text(), nullable=True),
        sa.Column(
            "posting_type",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'part_time'"),
        ),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column("discord_thread_id", sa.Text(), nullable=True),
        sa.Column("posted_by_discord_user_id", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crm_account_id", sa.Text(), nullable=True),
        sa.Column("erpnext_project_id", sa.Text(), nullable=True),
        sa.Column("kimai_project_id", sa.Text(), nullable=True),
        sa.Column("kimai_customer_id", sa.Text(), nullable=True),
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
            "lifecycle_stage IN ('pending_gig', 'project')",
            name="ck_engagements_lifecycle_stage",
        ),
        sa.CheckConstraint(
            "status IN ('recruiting', 'filled', 'unknown', 'lost', 'outdated')",
            name="ck_engagements_status",
        ),
        sa.CheckConstraint(
            "posting_type IN ('part_time', 'full_time', 'part_time_or_full_time', 'unknown')",
            name="ck_engagements_posting_type",
        ),
        sa.UniqueConstraint(
            "discord_message_id",
            name="uq_engagements_discord_message_id",
        ),
    )
    op.create_index("idx_engagements_status", "engagements", ["status"])
    op.create_index(
        "idx_engagements_posted_by_discord_user_id",
        "engagements",
        ["posted_by_discord_user_id"],
    )
    op.create_index(
        "idx_engagements_discord_thread_id",
        "engagements",
        ["discord_thread_id"],
    )

    op.create_table(
        "engagement_applications",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crm_contact_id", sa.Text(), nullable=True),
        sa.Column("discord_user_id", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'suggested'"),
        ),
        sa.Column(
            "source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'match_candidates'"),
        ),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("fit_score", sa.Float(), nullable=True),
        sa.Column(
            "evaluation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "status IN ('suggested', 'interested', 'reviewing', 'contacted', 'accepted', 'rejected', 'withdrawn')",
            name="ck_engagement_applications_status",
        ),
        sa.CheckConstraint(
            "source IN ('match_candidates', 'direct_interest', 'manual_add', 'discord', 'crm', 'erp')",
            name="ck_engagement_applications_source",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_engagement_applications_engagement_id",
        "engagement_applications",
        ["engagement_id"],
    )
    op.create_index(
        "idx_engagement_applications_crm_contact_id",
        "engagement_applications",
        ["crm_contact_id"],
    )
    op.create_index(
        "idx_engagement_applications_status",
        "engagement_applications",
        ["status"],
    )
    op.create_unique_constraint(
        "uq_engagement_applications_engagement_crm_contact",
        "engagement_applications",
        ["engagement_id", "crm_contact_id"],
    )
    op.create_unique_constraint(
        "uq_engagement_applications_engagement_discord_user",
        "engagement_applications",
        ["engagement_id", "discord_user_id"],
    )

    op.create_table(
        "engagement_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_discord_user_id", sa.Text(), nullable=True),
        sa.Column(
            "payload",
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
        sa.ForeignKeyConstraint(
            ["engagement_id"], ["engagements.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["actor_person_id"], ["people.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_engagement_events_engagement_id",
        "engagement_events",
        ["engagement_id", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION engagements_set_updated_at_fn()
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
        CREATE TRIGGER engagements_set_updated_at_tr
        BEFORE UPDATE ON engagements
        FOR EACH ROW
        EXECUTE FUNCTION engagements_set_updated_at_fn();
        """
    )
    op.execute(
        """
        CREATE FUNCTION engagement_applications_set_updated_at_fn()
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
        CREATE TRIGGER engagement_applications_set_updated_at_tr
        BEFORE UPDATE ON engagement_applications
        FOR EACH ROW
        EXECUTE FUNCTION engagement_applications_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop local engagement tracking tables."""
    op.execute(
        "DROP TRIGGER IF EXISTS engagement_applications_set_updated_at_tr ON engagement_applications"
    )
    op.execute("DROP FUNCTION IF EXISTS engagement_applications_set_updated_at_fn()")
    op.execute("DROP TRIGGER IF EXISTS engagements_set_updated_at_tr ON engagements")
    op.execute("DROP FUNCTION IF EXISTS engagements_set_updated_at_fn()")
    op.drop_index("idx_engagement_events_engagement_id", table_name="engagement_events")
    op.drop_table("engagement_events")
    op.drop_constraint(
        "uq_engagement_applications_engagement_discord_user",
        "engagement_applications",
        type_="unique",
    )
    op.drop_constraint(
        "uq_engagement_applications_engagement_crm_contact",
        "engagement_applications",
        type_="unique",
    )
    op.drop_index(
        "idx_engagement_applications_status", table_name="engagement_applications"
    )
    op.drop_index(
        "idx_engagement_applications_crm_contact_id",
        table_name="engagement_applications",
    )
    op.drop_index(
        "idx_engagement_applications_engagement_id",
        table_name="engagement_applications",
    )
    op.drop_table("engagement_applications")
    op.drop_index("idx_engagements_discord_thread_id", table_name="engagements")
    op.drop_index("idx_engagements_posted_by_discord_user_id", table_name="engagements")
    op.drop_index("idx_engagements_status", table_name="engagements")
    op.drop_table("engagements")
    op.drop_constraint(
        "ck_job_post_channels_posting_type",
        "job_post_channels",
        type_="check",
    )
    op.drop_column("job_post_channels", "posting_type")
