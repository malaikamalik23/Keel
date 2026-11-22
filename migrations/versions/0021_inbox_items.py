"""Capture inbox: whatever the user typed, Keel's suggestion for where it belongs, and where it was filed once approved.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="new"),
        sa.Column("suggestion", sa.JSON(), nullable=True),
        sa.Column("filed_as", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("filed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_inbox_items"),
    )
    op.create_index("ix_inbox_items_status", "inbox_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_inbox_items_status", table_name="inbox_items")
    op.drop_table("inbox_items")
