"""A task can be linked to a calendar event it has to be ready for ("prepare slides" -> Thursday's meeting).

The event is identified by its calendar UID and start time, never by its title. Existing tasks are unlinked.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("event_uid", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("event_start", sa.DateTime(), nullable=True))  # naive UTC, like every DateTime here


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("event_start")
        batch.drop_column("event_uid")
