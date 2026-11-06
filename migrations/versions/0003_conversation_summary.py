"""Phase 1 follow-up: conversation running summary, so long chats degrade gracefully instead of
just truncating at history_message_limit.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("summary", sa.Text(), nullable=True))
        batch.add_column(sa.Column("summarized_through_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("summarized_through_id")
        batch.drop_column("summary")
