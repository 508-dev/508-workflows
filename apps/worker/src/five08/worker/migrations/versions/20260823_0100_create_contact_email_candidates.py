"""Create durable review records for contact email intake."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260823_0100"
down_revision = "20260711_0100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Store proposed contacts before a dashboard user approves a CRM mutation."""
    op.create_table(
        "contact_email_candidates",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("message_id", sa.Text(), nullable=False, unique=True),
        sa.Column("delivered_to", sa.Text(), nullable=False),
        sa.Column("forwarded_by_name", sa.Text(), nullable=True),
        sa.Column("forwarded_by_email", sa.Text(), nullable=True),
        sa.Column("proposed_name", sa.Text(), nullable=True),
        sa.Column("proposed_email", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("extraction_method", sa.Text(), nullable=False),
        sa.Column("crm_contact_id", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending', 'approved', 'dismissed')",
            name="ck_contact_email_candidates_status",
        ),
    )
    op.create_index(
        "idx_contact_email_candidates_pending_created",
        "contact_email_candidates",
        ["status", "created_at"],
    )
    op.execute(
        """
        CREATE TRIGGER contact_email_candidates_set_updated_at
        BEFORE UPDATE ON contact_email_candidates
        FOR EACH ROW EXECUTE FUNCTION engagements_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop contact email review candidates."""
    op.execute(
        "DROP TRIGGER IF EXISTS contact_email_candidates_set_updated_at "
        "ON contact_email_candidates"
    )
    op.drop_index(
        "idx_contact_email_candidates_pending_created",
        table_name="contact_email_candidates",
    )
    op.drop_table("contact_email_candidates")
