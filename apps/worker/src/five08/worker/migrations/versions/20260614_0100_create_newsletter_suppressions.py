"""Create newsletter suppression registry."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260614_0100"
down_revision = "20260613_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create provider-independent newsletter suppression records."""
    op.create_table(
        "newsletter_suppressions",
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("source_provider", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
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
            "source_provider IN ('brevo', 'keila', 'manual')",
            name="ck_newsletter_suppressions_source_provider",
        ),
        sa.PrimaryKeyConstraint(
            "email",
            "source_provider",
            name="pk_newsletter_suppressions_email_source",
        ),
    )
    op.create_index(
        "idx_newsletter_suppressions_active_last_seen",
        "newsletter_suppressions",
        ["active", "last_seen_at"],
    )
    op.create_index(
        "idx_newsletter_suppressions_source_active",
        "newsletter_suppressions",
        ["source_provider", "active"],
    )
    op.execute(
        """
        CREATE FUNCTION newsletter_suppressions_set_updated_at_fn()
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
        CREATE TRIGGER newsletter_suppressions_set_updated_at_tr
        BEFORE UPDATE ON newsletter_suppressions
        FOR EACH ROW
        EXECUTE FUNCTION newsletter_suppressions_set_updated_at_fn();
        """
    )


def downgrade() -> None:
    """Drop newsletter suppression registry."""
    op.execute(
        "DROP TRIGGER IF EXISTS newsletter_suppressions_set_updated_at_tr "
        "ON newsletter_suppressions"
    )
    op.execute("DROP FUNCTION IF EXISTS newsletter_suppressions_set_updated_at_fn()")
    op.drop_index(
        "idx_newsletter_suppressions_source_active",
        table_name="newsletter_suppressions",
    )
    op.drop_index(
        "idx_newsletter_suppressions_active_last_seen",
        table_name="newsletter_suppressions",
    )
    op.drop_table("newsletter_suppressions")
