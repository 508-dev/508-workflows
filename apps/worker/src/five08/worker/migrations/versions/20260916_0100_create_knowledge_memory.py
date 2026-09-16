"""Create durable source-grounded knowledge memory."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260916_0100"
down_revision = "20260823_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create knowledge facts, provenance, and frozen capture drafts."""
    op.create_table(
        "memory_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'fact'"),
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "value_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("review_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("search_document", postgresql.TSVECTOR(), nullable=False),
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
            "scope_type IN ('user', 'project', 'org')",
            name="ck_memory_facts_scope_type",
        ),
        sa.CheckConstraint(
            "kind IN ('fact', 'qa', 'decision')",
            name="ck_memory_facts_kind",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'project', 'org')",
            name="ck_memory_facts_visibility",
        ),
        sa.CheckConstraint(
            "verification_status IN ("
            "'inferred', 'source_recorded', 'author_confirmed', "
            "'user_confirmed', 'admin_confirmed', 'authoritative'"
            ")",
            name="ck_memory_facts_verification_status",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'disputed', 'deleted')",
            name="ck_memory_facts_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_memory_facts_confidence",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["memory_facts.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_memory_facts_scope",
        "memory_facts",
        ["organization_id", "scope_type", "scope_id", "status"],
    )
    op.create_index(
        "uq_memory_facts_active_dedupe",
        "memory_facts",
        ["organization_id", "scope_type", "scope_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND deleted_at IS NULL AND dedupe_key IS NOT NULL"
        ),
    )
    op.create_index(
        "idx_memory_facts_search",
        "memory_facts",
        ["search_document"],
        postgresql_using="gin",
    )

    op.create_table(
        "memory_fact_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_title", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_excerpt", sa.Text(), nullable=True),
        sa.Column("source_excerpt_hash", sa.Text(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "message_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "author_ids",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["fact_id"], ["memory_facts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "fact_id",
            "source_ref",
            "source_excerpt_hash",
            name="uq_memory_fact_sources_fact_ref_excerpt",
        ),
    )
    op.create_index(
        "idx_memory_fact_sources_fact_id",
        "memory_fact_sources",
        ["fact_id", "created_at"],
    )

    op.create_table(
        "knowledge_capture_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.Text(), nullable=False),
        sa.Column(
            "source_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "message_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "candidate_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "confirmed_fact_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "scope_type IN ('user', 'project', 'org')",
            name="ck_knowledge_capture_drafts_scope_type",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'project', 'org')",
            name="ck_knowledge_capture_drafts_visibility",
        ),
    )
    op.create_index(
        "idx_knowledge_capture_drafts_actor",
        "knowledge_capture_drafts",
        ["actor_id", "expires_at"],
    )

    op.execute(
        """
        CREATE FUNCTION memory_facts_set_updated_at_fn()
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
        CREATE TRIGGER memory_facts_set_updated_at_tr
        BEFORE UPDATE ON memory_facts
        FOR EACH ROW
        EXECUTE FUNCTION memory_facts_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop durable knowledge storage."""
    op.execute("DROP TRIGGER IF EXISTS memory_facts_set_updated_at_tr ON memory_facts")
    op.execute("DROP FUNCTION IF EXISTS memory_facts_set_updated_at_fn()")
    op.drop_index(
        "idx_knowledge_capture_drafts_actor",
        table_name="knowledge_capture_drafts",
    )
    op.drop_table("knowledge_capture_drafts")
    op.drop_index("idx_memory_fact_sources_fact_id", table_name="memory_fact_sources")
    op.drop_table("memory_fact_sources")
    op.drop_index("idx_memory_facts_search", table_name="memory_facts")
    op.drop_index("uq_memory_facts_active_dedupe", table_name="memory_facts")
    op.drop_index("idx_memory_facts_scope", table_name="memory_facts")
    op.drop_table("memory_facts")
