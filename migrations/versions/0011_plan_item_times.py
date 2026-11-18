"""Plan items get a time block (local HH:MM) when the planner fits them into the day.

Existing plan items keep no times: they were planned without a schedule.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_items") as batch:
        batch.add_column(sa.Column("start_time", sa.String(length=5), nullable=True))
        batch.add_column(sa.Column("end_time", sa.String(length=5), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("plan_items") as batch:
        batch.drop_column("end_time")
        batch.drop_column("start_time")
