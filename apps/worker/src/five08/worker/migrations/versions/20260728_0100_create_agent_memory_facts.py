"""Create durable agent memory fact storage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260728_0100"
down_revision = "20260711_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create provenance-aware durable memory facts with expiry cleanup."""
    op.create_table(
        "agent_memory_facts",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_excerpt_hash", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "char_length(btrim(organization_id)) BETWEEN 1 AND 128",
            name="ck_agent_memory_facts_organization_id_length",
        ),
        sa.CheckConstraint(
            "scope_type IN ('user', 'project', 'org')",
            name="ck_agent_memory_facts_scope_type",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'project', 'org')",
            name="ck_agent_memory_facts_visibility",
        ),
        sa.CheckConstraint(
            "verification_status IN "
            "('inferred', 'user_confirmed', 'admin_confirmed', 'authoritative')",
            name="ck_agent_memory_facts_verification_status",
        ),
        sa.CheckConstraint(
            "char_length(btrim(key)) BETWEEN 1 AND 128",
            name="ck_agent_memory_facts_key_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(value_json) = 'object'",
            name="ck_agent_memory_facts_value_json_object",
        ),
        sa.CheckConstraint(
            "octet_length(value_json::text) <= 8192",
            name="ck_agent_memory_facts_value_json_size",
        ),
        sa.CheckConstraint(
            "(scope_type = 'user' AND visibility = 'private') "
            "OR (scope_type = 'project' AND visibility = 'project') "
            "OR (scope_type = 'org' AND visibility = 'org')",
            name="ck_agent_memory_facts_scope_visibility",
        ),
        sa.CheckConstraint(
            "scope_type != 'org' OR scope_id = organization_id",
            name="ck_agent_memory_facts_org_scope_matches_tenant",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_agent_memory_facts_confidence_range",
        ),
    )
    op.create_index(
        "idx_agent_memory_facts_tenant_visible_scope",
        "agent_memory_facts",
        ["organization_id", "scope_type", "scope_id", "visibility", "created_at"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_agent_memory_facts_tenant_expires_at",
        "agent_memory_facts",
        ["organization_id", "expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL AND deleted_at IS NULL"),
    )
    op.create_index(
        "idx_agent_memory_facts_tenant_deleted_at",
        "agent_memory_facts",
        ["organization_id", "deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "idx_agent_memory_facts_tenant_id",
        "agent_memory_facts",
        ["organization_id", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION agent_memory_facts_set_updated_at_fn()
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
        CREATE TRIGGER agent_memory_facts_set_updated_at_tr
        BEFORE UPDATE ON agent_memory_facts
        FOR EACH ROW
        EXECUTE FUNCTION agent_memory_facts_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop durable agent memory fact storage."""
    op.execute(
        "DROP TRIGGER IF EXISTS agent_memory_facts_set_updated_at_tr "
        "ON agent_memory_facts"
    )
    op.execute("DROP FUNCTION IF EXISTS agent_memory_facts_set_updated_at_fn()")
    op.drop_index(
        "idx_agent_memory_facts_tenant_id",
        table_name="agent_memory_facts",
    )
    op.drop_index(
        "idx_agent_memory_facts_tenant_deleted_at",
        table_name="agent_memory_facts",
    )
    op.drop_index(
        "idx_agent_memory_facts_tenant_expires_at",
        table_name="agent_memory_facts",
    )
    op.drop_index(
        "idx_agent_memory_facts_tenant_visible_scope",
        table_name="agent_memory_facts",
    )
    op.drop_table("agent_memory_facts")
