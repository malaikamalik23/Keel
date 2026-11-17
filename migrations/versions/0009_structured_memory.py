"""Structured memory: category, an optional profile topic, where each memory came from, and when it was last confirmed
and last used (for review of outdated memories).

Existing memories keep their content and kind. They get category "general", source "unknown" (it was never recorded),
confirmed_at = their last update, and no topic or last-use date.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("category", sa.String(length=16), nullable=False, server_default="general"))
        batch.add_column(sa.Column("topic", sa.String(length=60), nullable=True))
        batch.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="unknown"))
        batch.add_column(sa.Column("confirmed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_used_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_memories_category", ["category"])
        batch.create_index("ix_memories_topic", ["topic"])
    op.execute("UPDATE memories SET confirmed_at = COALESCE(updated_at, created_at)")


def downgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_topic")
        batch.drop_index("ix_memories_category")
        batch.drop_column("last_used_at")
        batch.drop_column("confirmed_at")
        batch.drop_column("source")
        batch.drop_column("topic")
        batch.drop_column("category")
