"""Link each revised wiki proposal to its immutable parent proposal."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260917_0400"
down_revision = "20260917_0300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add an immutable, optional self-reference for proposal revisions."""
    op.add_column(
        "wiki_edit_proposals",
        sa.Column(
            "revision_parent_proposal_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_wiki_edit_proposals_revision_parent",
        "wiki_edit_proposals",
        "wiki_edit_proposals",
        ["revision_parent_proposal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_wiki_edit_proposals_revision_parent_not_self",
        "wiki_edit_proposals",
        "revision_parent_proposal_id IS NULL OR revision_parent_proposal_id <> id",
    )

    # The parent is assigned while inserting a new revision. Subsequent
    # lifecycle updates must not rewrite the proposal lineage.
    op.execute(
        """
        CREATE FUNCTION wiki_edit_proposals_preserve_revision_parent_fn()
        RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.revision_parent_proposal_id
                    IS DISTINCT FROM OLD.revision_parent_proposal_id THEN
                RAISE EXCEPTION 'wiki proposal revision parent is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER wiki_edit_proposals_preserve_revision_parent_tr
        BEFORE UPDATE ON wiki_edit_proposals
        FOR EACH ROW
        EXECUTE FUNCTION wiki_edit_proposals_preserve_revision_parent_fn();
        """
    )


def downgrade() -> None:
    """Remove the proposal lineage field and its immutability guard."""
    op.execute(
        "DROP TRIGGER IF EXISTS wiki_edit_proposals_preserve_revision_parent_tr "
        "ON wiki_edit_proposals"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS wiki_edit_proposals_preserve_revision_parent_fn()"
    )
    op.drop_constraint(
        "ck_wiki_edit_proposals_revision_parent_not_self",
        "wiki_edit_proposals",
        type_="check",
    )
    op.drop_constraint(
        "fk_wiki_edit_proposals_revision_parent",
        "wiki_edit_proposals",
        type_="foreignkey",
    )
    op.drop_column("wiki_edit_proposals", "revision_parent_proposal_id")
