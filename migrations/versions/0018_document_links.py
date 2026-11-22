"""Document links: a note or file in NOTES_DIR connected to an application, a task or an email.

Links are keyed by the file's path (relative to NOTES_DIR), not the index row, because re-indexing an edited file
replaces its row; Keel updates the path when it moves a file. Projects keep their own links (project_links).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_links"),
        sa.UniqueConstraint("path", "target_type", "target_id", name="uq_document_links_path"),
    )
    op.create_index("ix_document_links_path", "document_links", ["path"])
    op.create_index("ix_document_links_target", "document_links", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_document_links_target", table_name="document_links")
    op.drop_index("ix_document_links_path", table_name="document_links")
    op.drop_table("document_links")
