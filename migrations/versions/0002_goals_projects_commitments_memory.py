"""Phase 2: goals, milestones, projects, commitments, memories; link tasks to them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("horizon", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_goals"),
    )
    op.create_index("ix_goals_status", "goals", ["status"])

    op.create_table(
        "milestones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("goal_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_milestones_goal_id_goals", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_milestones"),
    )
    op.create_index("ix_milestones_goal_id", "milestones", ["goal_id"])

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("goal_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["goal_id"], ["goals.id"], name="fk_projects_goal_id_goals", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_goal_id", "projects", ["goal_id"])

    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("importance", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("postponement_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_commitments_project_id_projects", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name="fk_commitments_task_id_tasks", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_commitments_conversation_id_conversations", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commitments"),
    )
    op.create_index("ix_commitments_deadline", "commitments", ["deadline"])
    op.create_index("ix_commitments_status", "commitments", ["status"])

    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(length=8), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], name="fk_memories_conversation_id_conversations", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memories"),
    )
    op.create_index("ix_memories_kind", "memories", ["kind"])

    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("goal_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("milestone_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_tasks_project_id_projects", "projects", ["project_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_tasks_goal_id_goals", "goals", ["goal_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_tasks_milestone_id_milestones", "milestones", ["milestone_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_tasks_project_id", "tasks", ["project_id"])
    op.create_index("ix_tasks_goal_id", "tasks", ["goal_id"])
    op.create_index("ix_tasks_milestone_id", "tasks", ["milestone_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_milestone_id", table_name="tasks")
    op.drop_index("ix_tasks_goal_id", table_name="tasks")
    op.drop_index("ix_tasks_project_id", table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("fk_tasks_milestone_id_milestones", type_="foreignkey")
        batch.drop_constraint("fk_tasks_goal_id_goals", type_="foreignkey")
        batch.drop_constraint("fk_tasks_project_id_projects", type_="foreignkey")
        batch.drop_column("milestone_id")
        batch.drop_column("goal_id")
        batch.drop_column("project_id")

    op.drop_index("ix_memories_kind", table_name="memories")
    op.drop_table("memories")
    op.drop_index("ix_commitments_status", table_name="commitments")
    op.drop_index("ix_commitments_deadline", table_name="commitments")
    op.drop_table("commitments")
    op.drop_index("ix_projects_goal_id", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_milestones_goal_id", table_name="milestones")
    op.drop_table("milestones")
    op.drop_index("ix_goals_status", table_name="goals")
    op.drop_table("goals")
