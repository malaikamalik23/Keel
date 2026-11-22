"""A workflow run claims a step in the database before running it, so two processes (or the scheduler and an approval
arriving at the same moment) can never run the same step twice. The claim expires, so a crash mid-step is recovered
(the step is then reported as interrupted and waits for the user).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.add_column(sa.Column("claimed_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch:
        batch.drop_column("claimed_until")
