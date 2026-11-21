"""Project management: a log of updates, blockers and decisions per project, linked notes/links, and a GitHub repo.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("repo", sa.String(length=140), nullable=True))
    op.create_table(
        "project_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_project_entries_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_project_entries"),
    )
    op.create_index("ix_project_entries_project_id", "project_entries", ["project_id"])
    op.create_table(
        "project_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("target", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], name="fk_project_links_project_id_projects", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_project_links"),
    )
    op.create_index("ix_project_links_project_id", "project_links", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_links_project_id", table_name="project_links")
    op.drop_table("project_links")
    op.drop_index("ix_project_entries_project_id", table_name="project_entries")
    op.drop_table("project_entries")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("repo")
