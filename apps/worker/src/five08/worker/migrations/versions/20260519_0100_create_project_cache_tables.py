"""Create local project cache tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260519_0100"
down_revision = "20260518_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create source-agnostic project cache tables."""
    op.create_table(
        "projects",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("customer", sa.Text(), nullable=True),
        sa.Column("source_status", sa.Text(), nullable=True),
        sa.Column("project_type", sa.Text(), nullable=True),
        sa.Column("priority", sa.Text(), nullable=True),
        sa.Column("percent_complete", sa.Float(), nullable=True),
        sa.Column("expected_start_date", sa.Date(), nullable=True),
        sa.Column("expected_end_date", sa.Date(), nullable=True),
        sa.Column("actual_start_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "source_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_synced_at",
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
    )
    op.execute(
        """
        ALTER TABLE projects
        ADD CONSTRAINT check_percent_complete_range
        CHECK (
            percent_complete IS NULL
            OR (percent_complete >= 0 AND percent_complete <= 100)
        )
        """
    )
    op.create_index("idx_projects_display_name", "projects", ["display_name"])
    op.create_index("idx_projects_source_status", "projects", ["source_status"])

    op.create_table(
        "project_external_ids",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "source",
            "external_id",
            name="uq_project_external_ids_source_external_id",
        ),
    )
    op.create_index(
        "idx_project_external_ids_project_id",
        "project_external_ids",
        ["project_id"],
    )

    op.create_table(
        "project_roster_members",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column(
            "roster_kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'erp_users'"),
        ),
        sa.Column(
            "source_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_seen_at",
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id",
            "source",
            "source_user_id",
            name="uq_project_roster_members_project_source_user",
        ),
    )
    op.create_index(
        "idx_project_roster_members_project_id",
        "project_roster_members",
        ["project_id"],
    )
    op.create_index(
        "idx_project_roster_members_email",
        "project_roster_members",
        ["email"],
    )
    op.execute(
        "CREATE INDEX idx_project_roster_members_lower_email "
        "ON project_roster_members (LOWER(email))"
    )
    op.execute(
        "CREATE INDEX idx_project_roster_members_lower_source_user_id "
        "ON project_roster_members (LOWER(source_user_id))"
    )

    op.create_table(
        "project_wiki_matches",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("match_status", sa.Text(), nullable=False),
        sa.Column("wiki_row_key", sa.Text(), nullable=True),
        sa.Column("wiki_row_label", sa.Text(), nullable=True),
        sa.Column("wiki_row_section", sa.Text(), nullable=True),
        sa.Column(
            "source_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "confirmed_at",
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "project_id",
            "document_id",
            name="uq_project_wiki_matches_project_document",
        ),
        sa.CheckConstraint(
            "match_status IN ('confirmed', 'no_row')",
            name="check_project_wiki_matches_status",
        ),
    )
    op.create_index(
        "idx_project_wiki_matches_document_row",
        "project_wiki_matches",
        ["document_id", "wiki_row_key"],
    )

    op.execute(
        """
        CREATE FUNCTION projects_set_updated_at_fn()
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
        CREATE TRIGGER projects_set_updated_at_tr
        BEFORE UPDATE ON projects
        FOR EACH ROW
        EXECUTE FUNCTION projects_set_updated_at_fn();
        """
    )
    op.execute(
        """
        CREATE TRIGGER project_wiki_matches_set_updated_at_tr
        BEFORE UPDATE ON project_wiki_matches
        FOR EACH ROW
        EXECUTE FUNCTION projects_set_updated_at_fn();
        """
    )
    op.execute(
        """
        CREATE FUNCTION project_roster_members_set_updated_at_fn()
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
        CREATE TRIGGER project_roster_members_set_updated_at_tr
        BEFORE UPDATE ON project_roster_members
        FOR EACH ROW
        EXECUTE FUNCTION project_roster_members_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop local project cache tables."""
    op.execute(
        "DROP TRIGGER IF EXISTS project_roster_members_set_updated_at_tr ON project_roster_members"
    )
    op.execute("DROP FUNCTION IF EXISTS project_roster_members_set_updated_at_fn()")
    op.execute(
        "DROP TRIGGER IF EXISTS project_wiki_matches_set_updated_at_tr ON project_wiki_matches"
    )
    op.execute("DROP TRIGGER IF EXISTS projects_set_updated_at_tr ON projects")
    op.execute("DROP FUNCTION IF EXISTS projects_set_updated_at_fn()")
    op.execute("DROP INDEX IF EXISTS idx_project_wiki_matches_document_row")
    op.execute("DROP TABLE IF EXISTS project_wiki_matches")
    op.execute("DROP INDEX IF EXISTS idx_project_roster_members_lower_source_user_id")
    op.execute("DROP INDEX IF EXISTS idx_project_roster_members_lower_email")
    op.execute("DROP INDEX IF EXISTS idx_project_roster_members_email")
    op.execute("DROP INDEX IF EXISTS idx_project_roster_members_project_id")
    op.execute("DROP TABLE IF EXISTS project_roster_members")
    op.execute("DROP INDEX IF EXISTS idx_project_external_ids_project_id")
    op.execute("DROP TABLE IF EXISTS project_external_ids")
    op.execute("DROP INDEX IF EXISTS idx_projects_source_status")
    op.execute("DROP INDEX IF EXISTS idx_projects_display_name")
    op.execute(
        "ALTER TABLE projects DROP CONSTRAINT IF EXISTS check_percent_complete_range"
    )
    op.execute("DROP TABLE IF EXISTS projects")
