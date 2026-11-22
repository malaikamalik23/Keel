"""Memory lifecycle: how sure Keel is of a fact, whether it is still current, until when it holds, what replaced it, and
links between related memories.

- status: "current" or "outdated". An outdated memory is kept as history ("worked at Systems Ltd until 2026") but is
  never treated as true now: it is left out of the context Keel builds for each message and of searches by default.
- confidence: high (you said or confirmed it), medium (saved in chat), low (inferred; worth checking).
- valid_until: a fact that holds until a date ("in Lahore until December"); after it, it counts as outdated.
- replaced_by_id: when a fact changed, the old version points at the new one.
- memory_links: two memories that belong together (a project and its supervisor, a decision and its reason).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("memories") as batch:
        batch.add_column(sa.Column("status", sa.String(length=12), nullable=False, server_default="current"))
        batch.add_column(sa.Column("confidence", sa.String(length=8), nullable=False, server_default="medium"))
        batch.add_column(sa.Column("valid_until", sa.Date(), nullable=True))
        batch.add_column(sa.Column("replaced_by_id", sa.Integer(), nullable=True))
        batch.create_index("ix_memories_status", ["status"])
    # What you typed yourself was certain; everything else keeps the default.
    op.execute("UPDATE memories SET confidence = 'high' WHERE source = 'user'")
    op.create_table(
        "memory_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("a_id", sa.Integer(), nullable=False),
        sa.Column("b_id", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["a_id"], ["memories.id"], name="fk_memory_links_a_id_memories", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["b_id"], ["memories.id"], name="fk_memory_links_b_id_memories", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_memory_links"),
        sa.UniqueConstraint("a_id", "b_id", name="uq_memory_links_a_id"),
    )


def downgrade() -> None:
    op.drop_table("memory_links")
    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_status")
        batch.drop_column("replaced_by_id")
        batch.drop_column("valid_until")
        batch.drop_column("confidence")
        batch.drop_column("status")
