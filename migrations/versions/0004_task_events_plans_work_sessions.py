"""Phase 3: task_events, plans, plan_items, work_sessions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("task_title", sa.String(length=200), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_task_events_task_id_tasks", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_task_events"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_index("ix_task_events_type", "task_events", ["type"])
    op.create_index("ix_task_events_at", "task_events", ["at"])

    op.create_table(
        "plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_date", sa.Date(), nullable=False),
        sa.Column("capacity_minutes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_plans"),
    )
    op.create_index("ix_plans_plan_date", "plans", ["plan_date"], unique=True)

    op.create_table(
        "plan_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("planned_minutes", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_major", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], name="fk_plan_items_plan_id_plans", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_plan_items_task_id_tasks", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_plan_items"),
    )
    op.create_index("ix_plan_items_plan_id", "plan_items", ["plan_id"])
    op.create_index("ix_plan_items_task_id", "plan_items", ["task_id"])

    op.create_table(
        "work_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_work_sessions_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name="fk_work_sessions_project_id_projects", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_sessions"),
    )
    op.create_index("ix_work_sessions_task_id", "work_sessions", ["task_id"])
    op.create_index("ix_work_sessions_project_id", "work_sessions", ["project_id"])
    op.create_index("ix_work_sessions_started_at", "work_sessions", ["started_at"])


def downgrade() -> None:
    op.drop_table("work_sessions")
    op.drop_table("plan_items")
    op.drop_table("plans")
    op.drop_table("task_events")
