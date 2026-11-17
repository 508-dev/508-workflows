"""Require a durable owner acknowledgement before a wiki publish attempt."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260917_0300"
down_revision = "20260917_0200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Persist the exact review packet acknowledged by the proposal owner."""
    op.add_column(
        "wiki_edit_proposals",
        sa.Column("review_acknowledged_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "wiki_edit_proposals",
        sa.Column("review_acknowledged_content_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "wiki_edit_proposals",
        sa.Column(
            "review_acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_wiki_edit_proposals_review_acknowledgement",
        "wiki_edit_proposals",
        "(review_acknowledged_by IS NULL "
        " AND review_acknowledged_content_hash IS NULL "
        " AND review_acknowledged_at IS NULL) "
        "OR (review_acknowledged_by = actor_id "
        " AND review_acknowledged_content_hash ~ '^[0-9a-f]{64}$' "
        " AND review_acknowledged_at IS NOT NULL)",
    )

    # Keep the acknowledgement an append-only approval of the immutable
    # proposal output. Publishing can change lifecycle status later, but cannot
    # replace or clear the owner's recorded review packet.
    op.execute(
        """
        CREATE FUNCTION wiki_edit_proposals_preserve_review_acknowledgement_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.review_acknowledged_at IS NULL THEN
                IF NEW.review_acknowledged_by IS NOT NULL
                   OR NEW.review_acknowledged_content_hash IS NOT NULL
                   OR NEW.review_acknowledged_at IS NOT NULL THEN
                    IF NEW.status <> 'proposed'
                       OR NEW.review_acknowledged_by IS NULL
                       OR NEW.review_acknowledged_content_hash IS NULL
                       OR NEW.review_acknowledged_at IS NULL
                       OR NEW.review_acknowledged_by <> NEW.actor_id THEN
                        RAISE EXCEPTION
                            'wiki review acknowledgement must be set together by its requester while proposed';
                    END IF;
                END IF;
            ELSIF NEW.review_acknowledged_by
                    IS DISTINCT FROM OLD.review_acknowledged_by
               OR NEW.review_acknowledged_content_hash
                    IS DISTINCT FROM OLD.review_acknowledged_content_hash
               OR NEW.review_acknowledged_at
                    IS DISTINCT FROM OLD.review_acknowledged_at THEN
                RAISE EXCEPTION 'wiki review acknowledgement is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER wiki_edit_proposals_preserve_review_acknowledgement_tr
        BEFORE UPDATE ON wiki_edit_proposals
        FOR EACH ROW
        EXECUTE FUNCTION wiki_edit_proposals_preserve_review_acknowledgement_fn();
        """
    )


def downgrade() -> None:
    """Remove the acknowledgement fields and their immutability trigger."""
    op.execute(
        "DROP TRIGGER IF EXISTS wiki_edit_proposals_preserve_review_acknowledgement_tr "
        "ON wiki_edit_proposals"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS wiki_edit_proposals_preserve_review_acknowledgement_fn()"
    )
    op.drop_constraint(
        "ck_wiki_edit_proposals_review_acknowledgement",
        "wiki_edit_proposals",
        type_="check",
    )
    op.drop_column("wiki_edit_proposals", "review_acknowledged_at")
    op.drop_column("wiki_edit_proposals", "review_acknowledged_content_hash")
    op.drop_column("wiki_edit_proposals", "review_acknowledged_by")
