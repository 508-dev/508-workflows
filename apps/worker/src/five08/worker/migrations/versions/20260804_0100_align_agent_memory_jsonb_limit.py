"""Align the JSONB text check with compact application-side memory validation."""

from __future__ import annotations

from alembic import op


revision = "20260804_0100"
down_revision = "20260729_0100"
branch_labels = None
depends_on = None


# The API limits compact UTF-8 JSON to 8 KiB. PostgreSQL renders JSONB with a
# space after each structural colon/comma; a fact permits at most 50 items, so
# a 150-byte allowance accepts every valid compact payload without meaningfully
# widening the database guard for direct writes.
_COMPACT_VALUE_JSON_MAX_BYTES = 8_192
_JSONB_TEXT_RENDERING_ALLOWANCE_BYTES = 150
_VALUE_JSON_POSTGRES_TEXT_MAX_BYTES = (
    _COMPACT_VALUE_JSON_MAX_BYTES + _JSONB_TEXT_RENDERING_ALLOWANCE_BYTES
)


def upgrade() -> None:
    """Avoid rejecting valid compact JSON solely because JSONB adds whitespace."""

    op.drop_constraint(
        "ck_agent_memory_facts_value_json_size",
        "agent_memory_facts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_memory_facts_value_json_size",
        "agent_memory_facts",
        f"octet_length(value_json::text) <= {_VALUE_JSON_POSTGRES_TEXT_MAX_BYTES}",
    )


def downgrade() -> None:
    """Restore the original JSONB text-size constraint."""

    op.drop_constraint(
        "ck_agent_memory_facts_value_json_size",
        "agent_memory_facts",
        type_="check",
    )
    op.create_check_constraint(
        "ck_agent_memory_facts_value_json_size",
        "agent_memory_facts",
        f"octet_length(value_json::text) <= {_COMPACT_VALUE_JSON_MAX_BYTES}",
    )
