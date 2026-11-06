"""Phase 5: documents + chunks, and an FTS5 index over chunk text (SQLite).

The FTS5 table is created with raw SQL (SQLAlchemy has no model for virtual tables). Its rowid is kept equal to
chunks.id by app/services/documents.py. On non-SQLite databases the virtual table is skipped and notes search is
reported as unavailable.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("mtime", sa.Float(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("path", name="uq_documents_path"),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_chunks_document_id_documents", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"])
    if op.get_bind().dialect.name == "sqlite":
        op.execute("CREATE VIRTUAL TABLE chunk_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2')")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TABLE IF EXISTS chunk_fts")
    op.drop_table("chunks")
    op.drop_table("documents")
