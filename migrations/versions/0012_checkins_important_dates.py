"""Commitments get a check-in date (a day to ask how it's going, before the deadline); important dates get a table.

Existing commitments are unchanged (no check-in date).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commitments") as batch:
        batch.add_column(sa.Column("check_in_date", sa.Date(), nullable=True))
    op.create_table(
        "important_dates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("on_date", sa.Date(), nullable=False),
        sa.Column("yearly", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("category", sa.String(length=16), nullable=False, server_default="personal"),
        sa.Column("remind_days_before", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_important_dates"),
    )
    op.create_index("ix_important_dates_on_date", "important_dates", ["on_date"])


def downgrade() -> None:
    op.drop_index("ix_important_dates_on_date", table_name="important_dates")
    op.drop_table("important_dates")
    with op.batch_alter_table("commitments") as batch:
        batch.drop_column("check_in_date")
