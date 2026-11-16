"""Taint lasts for the whole conversation, not just while external content is in the window or the summary.

Replaces conversations.summary_tainted (0007) with conversations.tainted. Backfilled from summary_tainted and from any
stored message that holds fenced external content.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("tainted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(
        "UPDATE conversations SET tainted = 1 WHERE summary_tainted = 1 OR id IN "
        "(SELECT conversation_id FROM messages WHERE CAST(content AS TEXT) LIKE '%<untrusted_content%')"
    )
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("summary_tainted")


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("summary_tainted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE conversations SET summary_tainted = tainted WHERE summary IS NOT NULL AND summary != ''")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("tainted")
