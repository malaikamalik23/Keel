"""Documents: page numbers per chunk (for citations), page count (exact or estimated), and how much hidden text was
left out.

PDFs indexed before this have no page numbers, so they are marked for re-indexing on the next run.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chunks") as batch:
        batch.add_column(sa.Column("page", sa.Integer(), nullable=True))
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("pages", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("hidden_skipped", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("pages_estimated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE documents SET sha256 = '', mtime = -1 WHERE lower(path) LIKE '%.pdf'")


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("pages_estimated")
        batch.drop_column("hidden_skipped")
        batch.drop_column("pages")
    with op.batch_alter_table("chunks") as batch:
        batch.drop_column("page")
