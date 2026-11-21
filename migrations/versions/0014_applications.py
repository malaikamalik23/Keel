"""Applications manager: jobs, internships, scholarships and programmes the user is considering or applying to, and
the drafts Keel helps write for them (cover letters, statements of purpose, answers). Keel prepares; the user submits.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="job"),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("location", sa.String(length=120), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="interested"),
        sa.Column("eligibility", sa.Text(), nullable=True),
        sa.Column("documents", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("next_step", sa.String(length=200), nullable=True),
        sa.Column("next_step_date", sa.Date(), nullable=True),
        sa.Column("applied_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_opportunities"),
    )
    op.create_index("ix_opportunities_deadline", "opportunities", ["deadline"])
    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_table(
        "application_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["opportunity_id"], ["opportunities.id"], name="fk_application_drafts_opportunity_id_opportunities", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_application_drafts"),
    )
    op.create_index("ix_application_drafts_opportunity_id", "application_drafts", ["opportunity_id"])


def downgrade() -> None:
    op.drop_index("ix_application_drafts_opportunity_id", table_name="application_drafts")
    op.drop_table("application_drafts")
    op.drop_index("ix_opportunities_status", table_name="opportunities")
    op.drop_index("ix_opportunities_deadline", table_name="opportunities")
    op.drop_table("opportunities")
