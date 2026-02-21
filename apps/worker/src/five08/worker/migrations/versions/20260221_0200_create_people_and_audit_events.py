"""Create people cache and human audit tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "202602210200"
down_revision = "202602210100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create people and audit_events tables with updated_at triggers."""
    op.create_table(
        "people",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("crm_contact_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_508", sa.Text(), nullable=True),
        sa.Column("discord_user_id", sa.Text(), nullable=True),
        sa.Column("discord_username", sa.Text(), nullable=True),
        sa.Column(
            "discord_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("github_username", sa.Text(), nullable=True),
        sa.Column(
            "sync_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
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
            "sync_status IN ('active', 'missing_in_crm', 'conflict')",
            name="ck_people_sync_status",
        ),
        sa.UniqueConstraint("crm_contact_id", name="uq_people_crm_contact_id"),
        sa.UniqueConstraint("discord_user_id", name="uq_people_discord_user_id"),
    )

    op.create_index("idx_people_email", "people", ["email"])
    op.create_index("idx_people_email_508", "people", ["email_508"])
    op.create_index("idx_people_discord_user_id", "people", ["discord_user_id"])

    op.create_table(
        "audit_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=True),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("actor_provider", sa.Text(), nullable=False),
        sa.Column("actor_subject", sa.Text(), nullable=False),
        sa.Column("actor_display_name", sa.Text(), nullable=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "source IN ('discord', 'admin_dashboard')",
            name="ck_audit_events_source",
        ),
        sa.CheckConstraint(
            "result IN ('success', 'denied', 'error')",
            name="ck_audit_events_result",
        ),
        sa.CheckConstraint(
            "actor_provider IN ('discord', 'admin_sso')",
            name="ck_audit_events_actor_provider",
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="SET NULL"),
    )

    op.create_index("idx_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index(
        "idx_audit_events_source_action",
        "audit_events",
        ["source", "action", "occurred_at"],
    )
    op.create_index(
        "idx_audit_events_actor_lookup",
        "audit_events",
        ["actor_provider", "actor_subject", "occurred_at"],
    )
    op.create_index("idx_audit_events_person_id", "audit_events", ["person_id"])

    op.execute(
        """
        CREATE FUNCTION people_set_updated_at_fn()
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
        CREATE TRIGGER people_set_updated_at_tr
        BEFORE UPDATE ON people
        FOR EACH ROW
        EXECUTE FUNCTION people_set_updated_at_fn();
        """
    )

    op.execute(
        """
        CREATE FUNCTION audit_events_set_updated_at_fn()
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
        CREATE TRIGGER audit_events_set_updated_at_tr
        BEFORE UPDATE ON audit_events
        FOR EACH ROW
        EXECUTE FUNCTION audit_events_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop people and audit_events tables."""
    op.execute("DROP TRIGGER IF EXISTS audit_events_set_updated_at_tr ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_set_updated_at_fn()")
    op.execute("DROP TRIGGER IF EXISTS people_set_updated_at_tr ON people")
    op.execute("DROP FUNCTION IF EXISTS people_set_updated_at_fn()")

    op.drop_index("idx_audit_events_person_id", table_name="audit_events")
    op.drop_index("idx_audit_events_actor_lookup", table_name="audit_events")
    op.drop_index("idx_audit_events_source_action", table_name="audit_events")
    op.drop_index("idx_audit_events_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("idx_people_discord_user_id", table_name="people")
    op.drop_index("idx_people_email_508", table_name="people")
    op.drop_index("idx_people_email", table_name="people")
    op.drop_table("people")
