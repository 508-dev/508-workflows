"""Create durable, review-first wiki editing workflow records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260917_0200"
down_revision = "20260917_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add app-issued UUID workflow rows without requiring pgcrypto."""
    op.create_table(
        "wiki_edit_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("request_text", sa.Text(), nullable=False),
        sa.Column("instruction_hash", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("target_document_id", sa.Text(), nullable=True),
        sa.Column(
            "selected_conversation_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_wiki_edit_requests_org_idempotency",
        ),
    )
    op.create_index(
        "idx_wiki_edit_requests_actor_created",
        "wiki_edit_requests",
        ["organization_id", "actor_id", "created_at"],
    )

    op.create_table(
        "wiki_edit_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("target_action", sa.Text(), nullable=False),
        sa.Column("target_document_id", sa.Text(), nullable=True),
        sa.Column(
            "base_document_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("base_content_hash", sa.Text(), nullable=True),
        sa.Column("revision_instruction", sa.Text(), nullable=True),
        sa.Column(
            "proposed_title",
            sa.Text(),
            nullable=True,
        ),
        sa.Column("proposed_text", sa.Text(), nullable=True),
        sa.Column("proposed_diff", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "proposed_source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "omp_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "conflict_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("published_document_id", sa.Text(), nullable=True),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("published_document_version", sa.Text(), nullable=True),
        sa.Column("published_content_hash", sa.Text(), nullable=True),
        sa.Column("authoring_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["wiki_edit_requests.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "request_id",
            "revision",
            name="uq_wiki_edit_proposals_request_revision",
        ),
        sa.CheckConstraint("revision > 0", name="ck_wiki_edit_proposals_revision"),
        sa.CheckConstraint(
            "target_action IN ('create', 'update')",
            name="ck_wiki_edit_proposals_target_action",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'queued', 'authoring', 'proposed', 'conflict', 'failed', 'canceled', "
            "'publishing', 'published', 'publish_unknown'"
            ")",
            name="ck_wiki_edit_proposals_status",
        ),
        sa.CheckConstraint(
            "(target_action = 'create' AND target_document_id IS NULL "
            " AND base_document_payload IS NULL AND base_content_hash IS NULL) "
            "OR (target_action = 'update' AND target_document_id IS NOT NULL "
            " AND base_document_payload IS NOT NULL AND base_content_hash IS NOT NULL)",
            name="ck_wiki_edit_proposals_target_snapshot",
        ),
        sa.CheckConstraint(
            "status NOT IN ('proposed', 'publishing', 'published', 'publish_unknown') "
            "OR (output_committed_at IS NOT NULL AND proposed_title IS NOT NULL "
            " AND proposed_text IS NOT NULL AND proposed_diff IS NOT NULL "
            " AND summary IS NOT NULL)",
            name="ck_wiki_edit_proposals_output_for_review",
        ),
    )
    op.create_index(
        "idx_wiki_edit_proposals_org_status",
        "wiki_edit_proposals",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "idx_wiki_edit_proposals_target",
        "wiki_edit_proposals",
        ["organization_id", "target_document_id", "status"],
    )

    op.create_table(
        "wiki_edit_publish_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("target_action", sa.Text(), nullable=False),
        sa.Column("target_document_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("write_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["wiki_edit_proposals.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "proposal_id",
            name="uq_wiki_edit_publish_operations_proposal",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_wiki_edit_publish_operations_idempotency",
        ),
        sa.CheckConstraint(
            "target_action IN ('create', 'update')",
            name="ck_wiki_edit_publish_operations_target_action",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'write_started', 'succeeded', 'unknown', 'conflict')",
            name="ck_wiki_edit_publish_operations_status",
        ),
    )
    op.create_index(
        "idx_wiki_edit_publish_operations_org_status",
        "wiki_edit_publish_operations",
        ["organization_id", "status", "created_at"],
    )

    # Proposal payload columns are written once when authoring finishes. Later
    # lifecycle updates cannot rewrite a reviewed revision or its OMP run.
    op.execute(
        """
        CREATE FUNCTION wiki_edit_proposals_preserve_revision_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.request_id IS DISTINCT FROM OLD.request_id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
               OR NEW.revision IS DISTINCT FROM OLD.revision
               OR NEW.target_action IS DISTINCT FROM OLD.target_action
               OR NEW.target_document_id IS DISTINCT FROM OLD.target_document_id
               OR NEW.base_document_payload IS DISTINCT FROM OLD.base_document_payload
               OR NEW.base_content_hash IS DISTINCT FROM OLD.base_content_hash
               OR NEW.revision_instruction IS DISTINCT FROM OLD.revision_instruction
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'wiki proposal revision identity is immutable';
            END IF;

            IF OLD.omp_metadata IS NOT NULL
               AND NEW.omp_metadata IS DISTINCT FROM OLD.omp_metadata THEN
                RAISE EXCEPTION 'wiki proposal OMP metadata is immutable';
            END IF;
            IF OLD.omp_metadata IS NULL AND NEW.omp_metadata IS NOT NULL
               AND OLD.status <> 'queued' THEN
                RAISE EXCEPTION 'wiki proposal OMP metadata can only be bound at authoring start';
            END IF;

            IF OLD.output_committed_at IS NOT NULL
               AND (NEW.proposed_title IS DISTINCT FROM OLD.proposed_title
                    OR NEW.proposed_text IS DISTINCT FROM OLD.proposed_text
                    OR NEW.proposed_diff IS DISTINCT FROM OLD.proposed_diff
                    OR NEW.summary IS DISTINCT FROM OLD.summary
                    OR NEW.proposed_source_refs IS DISTINCT FROM OLD.proposed_source_refs
                    OR NEW.output_committed_at IS DISTINCT FROM OLD.output_committed_at) THEN
                RAISE EXCEPTION 'wiki proposal output is immutable';
            END IF;
            IF OLD.output_committed_at IS NULL
               AND (NEW.proposed_title IS DISTINCT FROM OLD.proposed_title
                    OR NEW.proposed_text IS DISTINCT FROM OLD.proposed_text
                    OR NEW.proposed_diff IS DISTINCT FROM OLD.proposed_diff
                    OR NEW.summary IS DISTINCT FROM OLD.summary
                    OR NEW.proposed_source_refs IS DISTINCT FROM OLD.proposed_source_refs
                    OR NEW.output_committed_at IS DISTINCT FROM OLD.output_committed_at)
               AND NOT (NEW.output_committed_at IS NOT NULL
                        AND NEW.status = 'proposed') THEN
                RAISE EXCEPTION 'wiki proposal output may only be committed once for review';
            END IF;

            IF OLD.conflict_payload IS NOT NULL
               AND NEW.conflict_payload IS DISTINCT FROM OLD.conflict_payload THEN
                RAISE EXCEPTION 'wiki proposal conflict details are immutable';
            END IF;
            IF OLD.failure_code IS NOT NULL
               AND NEW.failure_code IS DISTINCT FROM OLD.failure_code THEN
                RAISE EXCEPTION 'wiki proposal failure code is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER wiki_edit_proposals_preserve_revision_tr
        BEFORE UPDATE ON wiki_edit_proposals
        FOR EACH ROW
        EXECUTE FUNCTION wiki_edit_proposals_preserve_revision_fn();
        """
    )

    # The operation is intentionally recorded before the provider call. Once
    # write_started_at is set, a retry must reconcile rather than write again.
    op.execute(
        """
        CREATE FUNCTION wiki_edit_publish_operations_preserve_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.proposal_id IS DISTINCT FROM OLD.proposal_id
               OR NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.target_action IS DISTINCT FROM OLD.target_action
               OR NEW.target_document_id IS DISTINCT FROM OLD.target_document_id
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'wiki publish operation identity is immutable';
            END IF;
            IF OLD.write_started_at IS NOT NULL
               AND NEW.write_started_at IS DISTINCT FROM OLD.write_started_at THEN
                RAISE EXCEPTION 'wiki publish write start is immutable';
            END IF;
            IF OLD.result_payload IS NOT NULL
               AND NEW.result_payload IS DISTINCT FROM OLD.result_payload THEN
                RAISE EXCEPTION 'wiki publish result is immutable';
            END IF;
            IF OLD.resolved_at IS NOT NULL
               AND NEW.resolved_at IS DISTINCT FROM OLD.resolved_at THEN
                RAISE EXCEPTION 'wiki publish resolution is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER wiki_edit_publish_operations_preserve_tr
        BEFORE UPDATE ON wiki_edit_publish_operations
        FOR EACH ROW
        EXECUTE FUNCTION wiki_edit_publish_operations_preserve_fn();
        """
    )


def downgrade() -> None:
    """Remove wiki-editing workflow tables and their immutability triggers."""
    op.execute(
        "DROP TRIGGER IF EXISTS wiki_edit_publish_operations_preserve_tr "
        "ON wiki_edit_publish_operations"
    )
    op.execute("DROP FUNCTION IF EXISTS wiki_edit_publish_operations_preserve_fn()")
    op.execute(
        "DROP TRIGGER IF EXISTS wiki_edit_proposals_preserve_revision_tr "
        "ON wiki_edit_proposals"
    )
    op.execute("DROP FUNCTION IF EXISTS wiki_edit_proposals_preserve_revision_fn()")
    op.drop_index(
        "idx_wiki_edit_publish_operations_org_status",
        table_name="wiki_edit_publish_operations",
    )
    op.drop_table("wiki_edit_publish_operations")
    op.drop_index("idx_wiki_edit_proposals_target", table_name="wiki_edit_proposals")
    op.drop_index(
        "idx_wiki_edit_proposals_org_status",
        table_name="wiki_edit_proposals",
    )
    op.drop_table("wiki_edit_proposals")
    op.drop_index(
        "idx_wiki_edit_requests_actor_created",
        table_name="wiki_edit_requests",
    )
    op.drop_table("wiki_edit_requests")
