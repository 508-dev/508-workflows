"""Represent definitive no-write Outline rejections separately from unknown writes."""

from __future__ import annotations

from alembic import op

revision = "20260917_0500"
down_revision = "20260917_0400"
branch_labels = None
depends_on = None


_STATUS_CONSTRAINT = "ck_wiki_edit_publish_operations_status"
_TABLE = "wiki_edit_publish_operations"


def upgrade() -> None:
    """Permit audited definitive no-write rejections for wiki publish attempts."""
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _STATUS_CONSTRAINT,
        _TABLE,
        "status IN ("
        "'pending', 'write_started', 'succeeded', 'unknown', 'conflict', 'rejected'"
        ")",
    )


def downgrade() -> None:
    """Restore the original publish-operation status set."""
    # Preserve the existing safety barrier if a downgrade follows an explicit
    # rejection: older code understands ``unknown`` and will not retry it.
    op.execute(
        "UPDATE wiki_edit_publish_operations SET status = 'unknown' WHERE status = 'rejected'"
    )
    op.drop_constraint(_STATUS_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _STATUS_CONSTRAINT,
        _TABLE,
        "status IN ('pending', 'write_started', 'succeeded', 'unknown', 'conflict')",
    )
