"""Create durable bounded recurring agent schedules and run history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260728_0200"
down_revision = "20260728_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist frozen agent schedule envelopes before dispatching worker jobs."""
    op.create_table(
        "agent_schedules",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column("organization_id", sa.Text(), nullable=False),
        sa.Column("guild_id", sa.Text(), nullable=False),
        sa.Column("owner_discord_user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("cron_expression", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column(
            "definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "allowed_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
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
            name="ck_agent_schedules_organization_id_length",
        ),
        sa.CheckConstraint(
            "guild_id ~ '^[1-9][0-9]*$'",
            name="ck_agent_schedules_guild_id_snowflake",
        ),
        sa.CheckConstraint(
            "owner_discord_user_id ~ '^[1-9][0-9]*$'",
            name="ck_agent_schedules_owner_discord_user_id_snowflake",
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 140",
            name="ck_agent_schedules_name_length",
        ),
        sa.CheckConstraint(
            "char_length(btrim(cron_expression)) BETWEEN 9 AND 128",
            name="ck_agent_schedules_cron_expression_length",
        ),
        sa.CheckConstraint(
            "char_length(btrim(timezone)) BETWEEN 1 AND 128",
            name="ck_agent_schedules_timezone_length",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_agent_schedules_definition_object",
        ),
        sa.CheckConstraint(
            "octet_length(definition::text) <= 32768",
            name="ck_agent_schedules_definition_size",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_scopes) = 'array'",
            name="ck_agent_schedules_allowed_scopes_array",
        ),
        sa.CheckConstraint(
            "jsonb_array_length(allowed_scopes) >= 1",
            name="ck_agent_schedules_allowed_scopes_nonempty",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_agent_schedules_status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND next_run_at IS NOT NULL) "
            "OR (status IN ('paused', 'archived') AND next_run_at IS NULL)",
            name="ck_agent_schedules_status_next_run",
        ),
    )
    op.create_index(
        "idx_agent_schedules_active_next_run",
        "agent_schedules",
        ["next_run_at"],
        postgresql_where=sa.text("status = 'active' AND next_run_at IS NOT NULL"),
    )
    op.create_index(
        "idx_agent_schedules_guild_created_at",
        "agent_schedules",
        ["guild_id", "created_at"],
    )
    op.execute(
        """
        CREATE FUNCTION agent_schedules_set_updated_at_fn()
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
        CREATE TRIGGER agent_schedules_set_updated_at_tr
        BEFORE UPDATE ON agent_schedules
        FOR EACH ROW
        EXECUTE FUNCTION agent_schedules_set_updated_at_fn();
        """
    )

    op.create_table(
        "agent_schedule_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "schedule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.UniqueConstraint(
            "schedule_id",
            "occurrence_at",
            name="uq_agent_schedule_runs_schedule_occurrence",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual')",
            name="ck_agent_schedule_runs_trigger",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'skipped')",
            name="ck_agent_schedule_runs_status",
        ),
        sa.CheckConstraint(
            "output IS NULL OR char_length(output) <= 8000",
            name="ck_agent_schedule_runs_output_length",
        ),
        sa.CheckConstraint(
            "error IS NULL OR char_length(error) <= 2000",
            name="ck_agent_schedule_runs_error_length",
        ),
    )
    op.create_index(
        "idx_agent_schedule_runs_queued",
        "agent_schedule_runs",
        ["created_at"],
        postgresql_where=sa.text("status = 'queued' AND job_id IS NULL"),
    )
    op.create_index(
        "idx_agent_schedule_runs_schedule_occurrence",
        "agent_schedule_runs",
        ["schedule_id", "occurrence_at"],
    )
    op.execute(
        """
        CREATE FUNCTION agent_schedule_runs_set_updated_at_fn()
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
        CREATE TRIGGER agent_schedule_runs_set_updated_at_tr
        BEFORE UPDATE ON agent_schedule_runs
        FOR EACH ROW
        EXECUTE FUNCTION agent_schedule_runs_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop recurring agent schedule state and its supporting indexes."""
    op.execute(
        "DROP TRIGGER IF EXISTS agent_schedule_runs_set_updated_at_tr "
        "ON agent_schedule_runs"
    )
    op.execute("DROP FUNCTION IF EXISTS agent_schedule_runs_set_updated_at_fn()")
    op.drop_index(
        "idx_agent_schedule_runs_schedule_occurrence", table_name="agent_schedule_runs"
    )
    op.drop_index("idx_agent_schedule_runs_queued", table_name="agent_schedule_runs")
    op.drop_table("agent_schedule_runs")

    op.execute(
        "DROP TRIGGER IF EXISTS agent_schedules_set_updated_at_tr ON agent_schedules"
    )
    op.execute("DROP FUNCTION IF EXISTS agent_schedules_set_updated_at_fn()")
    op.drop_index("idx_agent_schedules_guild_created_at", table_name="agent_schedules")
    op.drop_index("idx_agent_schedules_active_next_run", table_name="agent_schedules")
    op.drop_table("agent_schedules")
