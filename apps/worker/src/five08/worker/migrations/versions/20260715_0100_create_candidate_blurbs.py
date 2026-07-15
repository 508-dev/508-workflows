"""Create versioned candidate blurb storage and cached profile summaries."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260715_0100"
down_revision = "20260711_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a durable, provenance-preserving candidate blurb library."""
    op.add_column("people", sa.Column("profile_summary", sa.Text(), nullable=True))

    # A composite reference lets the database enforce that a blurb's optional
    # application belongs to its engagement; a normal CHECK cannot cross tables.
    op.create_unique_constraint(
        "uq_engagement_applications_id_engagement",
        "engagement_applications",
        ["id", "engagement_id"],
    )

    op.create_table(
        "candidate_blurbs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("lineage_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("crm_contact_id", sa.Text(), nullable=True),
        sa.Column("discord_user_id", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_kind", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'approved'"),
        ),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("submitted_by_discord_user_id", sa.Text(), nullable=True),
        sa.Column("source_message_id", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
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
        sa.CheckConstraint("version >= 1", name="ck_candidate_blurbs_version"),
        sa.CheckConstraint(
            "scope IN ('general', 'gig')",
            name="ck_candidate_blurbs_scope",
        ),
        sa.CheckConstraint(
            """
            person_id IS NOT NULL
            OR NULLIF(btrim(crm_contact_id), '') IS NOT NULL
            OR NULLIF(btrim(discord_user_id), '') IS NOT NULL
            """,
            name="ck_candidate_blurbs_candidate_identity",
        ),
        sa.CheckConstraint(
            """
            (scope = 'general' AND engagement_id IS NULL AND application_id IS NULL)
            OR (scope = 'gig' AND engagement_id IS NOT NULL)
            """,
            name="ck_candidate_blurbs_scope_relation",
        ),
        sa.CheckConstraint(
            "btrim(text) <> ''",
            name="ck_candidate_blurbs_text_nonblank",
        ),
        sa.CheckConstraint(
            "author_kind IN ('candidate', 'candidate_attributed', 'team', 'ai')",
            name="ck_candidate_blurbs_author_kind",
        ),
        sa.CheckConstraint(
            """
            source IN (
                'dashboard',
                'discord_message',
                'discord_command',
                'discord_dm_paste',
                'discord_draft',
                'ai'
            )
            """,
            name="ck_candidate_blurbs_source",
        ),
        sa.CheckConstraint(
            """
            (status IN ('draft', 'approved') AND is_current)
            OR (status = 'superseded' AND NOT is_current)
            """,
            name="ck_candidate_blurbs_current_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_candidate_blurbs_metadata_object",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["candidate_blurbs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["engagement_id"],
            ["engagements.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "engagement_id"],
            ["engagement_applications.id", "engagement_applications.engagement_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "lineage_id",
            "version",
            name="uq_candidate_blurbs_lineage_version",
        ),
    )
    op.create_index(
        "uq_candidate_blurbs_lineage_current",
        "candidate_blurbs",
        ["lineage_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "idx_candidate_blurbs_person_current",
        "candidate_blurbs",
        ["person_id", "created_at"],
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "idx_candidate_blurbs_crm_contact_current",
        "candidate_blurbs",
        ["crm_contact_id", "created_at"],
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "idx_candidate_blurbs_discord_user_current",
        "candidate_blurbs",
        ["discord_user_id", "created_at"],
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "idx_candidate_blurbs_engagement_current",
        "candidate_blurbs",
        ["engagement_id", "created_at"],
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "idx_candidate_blurbs_application_current",
        "candidate_blurbs",
        ["application_id", "created_at"],
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    """Remove candidate blurb storage and the cached profile summary."""
    op.drop_index(
        "idx_candidate_blurbs_application_current", table_name="candidate_blurbs"
    )
    op.drop_index(
        "idx_candidate_blurbs_engagement_current", table_name="candidate_blurbs"
    )
    op.drop_index(
        "idx_candidate_blurbs_discord_user_current", table_name="candidate_blurbs"
    )
    op.drop_index(
        "idx_candidate_blurbs_crm_contact_current", table_name="candidate_blurbs"
    )
    op.drop_index("idx_candidate_blurbs_person_current", table_name="candidate_blurbs")
    op.drop_index("uq_candidate_blurbs_lineage_current", table_name="candidate_blurbs")
    op.drop_table("candidate_blurbs")
    op.drop_constraint(
        "uq_engagement_applications_id_engagement",
        "engagement_applications",
        type_="unique",
    )
    op.drop_column("people", "profile_summary")
