"""Phase 4: reminders, checkins, notifications.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("fire_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("linked_type", sa.String(length=16), nullable=True),
        sa.Column("linked_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reminders"),
    )
    op.create_index("ix_reminders_fire_at", "reminders", ["fire_at"])
    op.create_index("ix_reminders_status", "reminders", ["status"])

    op.create_table(
        "checkins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("checkin_date", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("user_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_checkins"),
    )
    op.create_index("ix_checkins_checkin_date", "checkins", ["checkin_date"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index("ix_notifications_kind", "notifications", ["kind"])
    op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"])
    op.create_index("ix_notifications_sent_at", "notifications", ["sent_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("checkins")
    op.drop_table("reminders")
