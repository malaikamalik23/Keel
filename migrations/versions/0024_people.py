"""People memory: who someone is, how often to keep in touch, and a log of when the user talked to them.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("aliases", sa.String(length=300), nullable=True),
        sa.Column("relation", sa.String(length=60), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=True),
        sa.Column("keep_in_touch_days", sa.Integer(), nullable=True),
        sa.Column("last_contact", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_people"),
    )
    op.create_index("ix_people_name", "people", ["name"])
    op.create_table(
        "person_interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("text", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], name="fk_person_interactions_person_id_people", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_person_interactions"),
    )
    op.create_index("ix_person_interactions_person_id", "person_interactions", ["person_id"])
    op.create_index("ix_person_interactions_on_date", "person_interactions", ["on_date"])


def downgrade() -> None:
    op.drop_table("person_interactions")
    op.drop_table("people")
