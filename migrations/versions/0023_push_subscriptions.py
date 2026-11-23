"""Push subscriptions: the devices where the user turned on notifications (the browser's push address and the keys that
encrypt each message for that device). Removing a row stops notifications to that device.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=1000), nullable=False),
        sa.Column("p256dh", sa.String(length=200), nullable=False),
        sa.Column("auth", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )


def downgrade() -> None:
    op.drop_table("push_subscriptions")
