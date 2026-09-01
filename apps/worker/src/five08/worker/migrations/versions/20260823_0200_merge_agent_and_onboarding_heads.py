"""Merge the agent and onboarding migration heads."""

from __future__ import annotations


revision = "20260823_0200"
down_revision = ("20260815_0100", "20260823_0100")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join independently deployed schema branches without altering tables."""


def downgrade() -> None:
    """Split the revision graph back into its prior independent heads."""
