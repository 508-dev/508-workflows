"""Add job-match fields to the people table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260304_0100"
down_revision = "202602210201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add profile and skill columns needed for candidate matching."""
    op.add_column("people", sa.Column("contact_type", sa.Text(), nullable=True))
    op.add_column(
        "people",
        sa.Column(
            "is_member",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("people", sa.Column("address_country", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("address_city", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("timezone", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("seniority", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("linkedin", sa.Text(), nullable=True))
    op.add_column(
        "people",
        sa.Column(
            "skills",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.add_column(
        "people",
        sa.Column(
            "skill_attrs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("people", sa.Column("latest_resume_id", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("latest_resume_name", sa.Text(), nullable=True))

    # GIN index for fast skill containment/overlap queries.
    # Created concurrently to avoid write-blocking locks on the people table.
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_people_skills",
            "people",
            ["skills"],
            postgresql_using="gin",
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_people_is_member",
            "people",
            ["is_member"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_people_seniority",
            "people",
            ["seniority"],
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_people_address_country",
            "people",
            ["address_country"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    """Remove job-match fields from the people table."""
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_people_address_country",
            table_name="people",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "idx_people_seniority",
            table_name="people",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "idx_people_is_member",
            table_name="people",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "idx_people_skills",
            table_name="people",
            postgresql_concurrently=True,
        )

    op.drop_column("people", "latest_resume_name")
    op.drop_column("people", "latest_resume_id")
    op.drop_column("people", "skill_attrs")
    op.drop_column("people", "skills")
    op.drop_column("people", "linkedin")
    op.drop_column("people", "seniority")
    op.drop_column("people", "timezone")
    op.drop_column("people", "address_city")
    op.drop_column("people", "address_country")
    op.drop_column("people", "is_member")
    op.drop_column("people", "contact_type")
