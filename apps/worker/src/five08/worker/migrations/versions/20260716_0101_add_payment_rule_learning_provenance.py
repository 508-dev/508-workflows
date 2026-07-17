"""Add durable provenance for feedback-derived payment suggestion rules."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0101"
down_revision = "20260716_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Give each payment rule an explicit configured/learned provenance."""
    op.add_column(
        "automation_rules",
        sa.Column(
            "origin",
            sa.Text(),
            nullable=False,
            server_default="configured",
        ),
    )
    op.add_column(
        "automation_rules", sa.Column("learning_key", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_automation_rules_origin",
        "automation_rules",
        "origin IN ('configured', 'learned')",
    )
    op.create_check_constraint(
        "ck_automation_rules_learning_provenance",
        "automation_rules",
        "(origin = 'configured' AND learning_key IS NULL) OR "
        "(origin = 'learned' AND learning_key IS NOT NULL)",
    )
    op.create_index(
        "uq_automation_rules_learning_key",
        "automation_rules",
        ["learning_key"],
        unique=True,
        postgresql_where=sa.text("learning_key IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove learned-rule provenance while retaining the base automation schema."""
    op.drop_index("uq_automation_rules_learning_key", table_name="automation_rules")
    op.drop_constraint(
        "ck_automation_rules_learning_provenance",
        "automation_rules",
        type_="check",
    )
    op.drop_constraint(
        "ck_automation_rules_origin",
        "automation_rules",
        type_="check",
    )
    op.drop_column("automation_rules", "learning_key")
    op.drop_column("automation_rules", "origin")
