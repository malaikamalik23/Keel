"""Remember whether a conversation's running summary was built from external content, so taint survives summarization.

Existing summaries are marked tainted: there is no way to know now what they were built from, and the safe default is
that writes in those conversations keep needing approval.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("summary_tainted", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE conversations SET summary_tainted = 1 WHERE summary IS NOT NULL AND summary != ''")


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("summary_tainted")
