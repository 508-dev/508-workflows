"""Add address_state to the people table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260321_0100"
down_revision = "20260304_0300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add address_state for richer candidate location formatting."""
    op.add_column("people", sa.Column("address_state", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove address_state from the people table."""
    op.drop_column("people", "address_state")
