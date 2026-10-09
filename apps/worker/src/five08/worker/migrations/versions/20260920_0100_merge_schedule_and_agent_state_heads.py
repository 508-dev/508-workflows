"""Merge the schedule and agent-state migration heads."""

from __future__ import annotations


revision = "20260920_0100"
down_revision = ("20260823_0200", "20260917_0100")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join independently deployed schema branches without altering tables."""


def downgrade() -> None:
    """Split the revision graph back into its prior independent heads."""
