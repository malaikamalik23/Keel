"""User settings changed from the dashboard: integration switches and retention rules.

Key-value, so a new switch or rule needs no migration. Keys: "switch:<integration>" -> true/false,
"retention:<category>" -> days (null = keep forever), "retention:last_run" -> ISO timestamp.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_user_settings"),
    )


def downgrade() -> None:
    op.drop_table("user_settings")
