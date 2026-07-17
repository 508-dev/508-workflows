"""Create typed automation, bank transaction, and project payment tables."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260716_0100"
down_revision = "20260711_0100"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[Any]]:
    """Shared timestamp columns for durable operational records."""
    return [
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
    ]


def upgrade() -> None:
    """Add a durable event/action layer and payment notification outbox."""
    op.create_table(
        "automation_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Text(), nullable=False),
        sa.Column("subject_revision", sa.Text(), nullable=True),
        sa.Column(
            "subject_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "facts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("event_key", name="uq_automation_events_event_key"),
    )
    op.create_index(
        "idx_automation_events_type_occurred_at",
        "automation_events",
        ["event_type", "occurred_at"],
    )

    op.create_table(
        "automation_rules",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mode", sa.Text(), nullable=False, server_default="suggest"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "actions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_automation_rules_project_id"
        ),
        sa.CheckConstraint(
            "mode IN ('observe', 'suggest', 'automatic')",
            name="ck_automation_rules_mode",
        ),
        sa.CheckConstraint("version >= 1", name="ck_automation_rules_version"),
        *_timestamps(),
    )
    op.create_index(
        "idx_automation_rules_event_enabled_priority",
        "automation_rules",
        ["event_type", "enabled", "priority"],
    )
    op.create_index(
        "idx_automation_rules_project_id", "automation_rules", ["project_id"]
    )

    op.create_table(
        "automation_rule_evaluations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("rule_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=False),
        sa.Column(
            "condition_trace",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rule_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["automation_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["rule_id"], ["automation_rules.id"]),
        sa.ForeignKeyConstraint(["rule_project_id"], ["projects.id"]),
        sa.UniqueConstraint(
            "event_id",
            "rule_id",
            "rule_version",
            name="uq_automation_rule_evaluation_version",
        ),
    )
    op.create_index(
        "idx_automation_rule_evaluations_event_id",
        "automation_rule_evaluations",
        ["event_id"],
    )
    op.create_index(
        "idx_automation_rule_evaluations_rule_id",
        "automation_rule_evaluations",
        ["rule_id"],
    )

    op.create_table(
        "automation_actions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_evaluation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("disposition", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_decision", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"], ["automation_events.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["rule_evaluation_id"],
            ["automation_rule_evaluations.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "mode IN ('observe', 'suggest', 'automatic')",
            name="ck_automation_actions_mode",
        ),
        sa.CheckConstraint(
            "disposition IN ('observed', 'suggested', 'ready')",
            name="ck_automation_actions_disposition",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'awaiting_review', 'approved', 'running', 'succeeded', 'failed', 'dead')",
            name="ck_automation_actions_status",
        ),
        sa.CheckConstraint(
            "review_decision IS NULL OR review_decision IN ('approved', 'rejected')",
            name="ck_automation_actions_review_decision",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_automation_actions_idempotency_key"
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_automation_actions_status_created_at",
        "automation_actions",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_automation_actions_event_id", "automation_actions", ["event_id"]
    )
    op.create_index(
        "idx_automation_actions_rule_evaluation_id",
        "automation_actions",
        ["rule_evaluation_id"],
    )
    op.create_index(
        "idx_automation_actions_review_decision_reviewed_at",
        "automation_actions",
        ["review_decision", "reviewed_at"],
    )

    op.create_table(
        "bank_transactions",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("source_revision", sa.Text(), nullable=True),
        sa.Column("source_modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transaction_id", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bank_account", sa.Text(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("counterparty", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference_number", sa.Text(), nullable=True),
        sa.Column(
            "reconciliation_entries",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "source_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_bank_transactions_direction",
        ),
        sa.CheckConstraint("amount > 0", name="ck_bank_transactions_amount"),
        sa.UniqueConstraint(
            "source", "external_id", name="uq_bank_transactions_source_external_id"
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_bank_transactions_source_transaction_id",
        "bank_transactions",
        ["source", "transaction_id"],
    )
    op.create_index(
        "idx_bank_transactions_posted_at", "bank_transactions", ["posted_at"]
    )

    op.create_table(
        "project_payment_allocations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "payment_transaction_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("superseded_reason", sa.Text(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("automation_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["payment_transaction_id"], ["bank_transactions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["automation_action_id"], ["automation_actions.id"]),
        sa.CheckConstraint("amount > 0", name="ck_project_payment_allocations_amount"),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'superseded')",
            name="ck_project_payment_allocations_status",
        ),
        sa.CheckConstraint(
            "match_method IN ('erp_reconciled', 'configured_rule', 'manual', 'learned_suggestion')",
            name="ck_project_payment_allocations_match_method",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_project_payment_allocations_idempotency_key"
        ),
        sa.UniqueConstraint(
            "automation_action_id",
            name="uq_project_payment_allocations_automation_action_id",
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_project_payment_allocations_project_status",
        "project_payment_allocations",
        ["project_id", "status"],
    )
    op.create_index(
        "idx_project_payment_allocations_transaction_id",
        "project_payment_allocations",
        ["payment_transaction_id"],
    )

    op.create_table(
        "project_discord_channels",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("channel_name", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("registered_by_discord_user_id", sa.Text(), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verification_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        *_timestamps(),
    )
    op.create_index(
        "uq_project_discord_channels_active_target",
        "project_discord_channels",
        ["guild_id", "channel_id"],
        unique=True,
        postgresql_where=sa.text("active IS TRUE"),
    )
    op.create_index(
        "idx_project_discord_channels_project_active",
        "project_discord_channels",
        ["project_id", "active"],
    )

    op.create_table(
        "project_payment_notification_outbox",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("allocation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_discord_channel_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["allocation_id"], ["project_payment_allocations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_discord_channel_id"],
            ["project_discord_channels.id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'blocked', 'failed', 'dead')",
            name="ck_project_payment_notification_outbox_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_project_payment_notification_outbox_key"
        ),
        sa.UniqueConstraint(
            "allocation_id",
            "project_discord_channel_id",
            name="uq_project_payment_notification_outbox_allocation_channel",
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_project_payment_notification_outbox_status_created",
        "project_payment_notification_outbox",
        ["status", "created_at"],
    )

    # The bot owns this receipt/lease. It follows the worker outbox so the
    # receipt can hold an FK to its canonical notification and mapping rather
    # than trusting request-provided payment/channel fields.
    op.create_table(
        "project_payment_discord_deliveries",
        sa.Column(
            "notification_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "project_discord_channel_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="sending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("discord_message_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["project_payment_notification_outbox.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_discord_channel_id"],
            ["project_discord_channels.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "status IN ('sending', 'sent', 'failed')",
            name="ck_project_payment_discord_deliveries_status",
        ),
        *_timestamps(),
    )
    op.create_index(
        "idx_project_payment_discord_deliveries_status_locked",
        "project_payment_discord_deliveries",
        ["status", "locked_at"],
    )


def downgrade() -> None:
    """Remove payment automation tables in dependent order."""
    op.drop_index(
        "idx_project_payment_discord_deliveries_status_locked",
        table_name="project_payment_discord_deliveries",
    )
    op.drop_table("project_payment_discord_deliveries")
    op.drop_index(
        "idx_project_payment_notification_outbox_status_created",
        table_name="project_payment_notification_outbox",
    )
    op.drop_table("project_payment_notification_outbox")
    op.drop_index(
        "idx_project_discord_channels_project_active",
        table_name="project_discord_channels",
    )
    op.drop_index(
        "uq_project_discord_channels_active_target",
        table_name="project_discord_channels",
    )
    op.drop_table("project_discord_channels")
    op.drop_index(
        "idx_project_payment_allocations_transaction_id",
        table_name="project_payment_allocations",
    )
    op.drop_index(
        "idx_project_payment_allocations_project_status",
        table_name="project_payment_allocations",
    )
    op.drop_table("project_payment_allocations")
    op.drop_index("idx_bank_transactions_posted_at", table_name="bank_transactions")
    op.drop_index(
        "idx_bank_transactions_source_transaction_id", table_name="bank_transactions"
    )
    op.drop_table("bank_transactions")
    op.drop_index(
        "idx_automation_actions_status_created_at", table_name="automation_actions"
    )
    op.drop_index(
        "idx_automation_actions_review_decision_reviewed_at",
        table_name="automation_actions",
    )
    op.drop_index(
        "idx_automation_actions_rule_evaluation_id",
        table_name="automation_actions",
    )
    op.drop_index("idx_automation_actions_event_id", table_name="automation_actions")
    op.drop_table("automation_actions")
    op.drop_index(
        "idx_automation_rule_evaluations_rule_id",
        table_name="automation_rule_evaluations",
    )
    op.drop_index(
        "idx_automation_rule_evaluations_event_id",
        table_name="automation_rule_evaluations",
    )
    op.drop_table("automation_rule_evaluations")
    op.drop_index("idx_automation_rules_project_id", table_name="automation_rules")
    op.drop_index(
        "idx_automation_rules_event_enabled_priority", table_name="automation_rules"
    )
    op.drop_table("automation_rules")
    op.drop_index(
        "idx_automation_events_type_occurred_at", table_name="automation_events"
    )
    op.drop_table("automation_events")
