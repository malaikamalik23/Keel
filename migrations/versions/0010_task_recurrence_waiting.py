"""Tasks: repeat on a schedule, wait on another task, wait on someone (with a follow-up date).

Existing tasks are unchanged: no recurrence, no blocker, not waiting on anyone.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("recurrence", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("blocked_by_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("waiting_on", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("follow_up_date", sa.Date(), nullable=True))
        batch.create_foreign_key("fk_tasks_blocked_by_id_tasks", "tasks", ["blocked_by_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_tasks_follow_up_date", ["follow_up_date"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_follow_up_date")
        batch.drop_constraint("fk_tasks_blocked_by_id_tasks", type_="foreignkey")
        batch.drop_column("follow_up_date")
        batch.drop_column("waiting_on")
        batch.drop_column("blocked_by_id")
        batch.drop_column("recurrence")
