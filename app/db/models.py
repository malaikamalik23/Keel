"""Database models. Schema changes go through Alembic migrations (see migrations/)."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    """Naive UTC timestamp. All DateTime columns store naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


# Explicit constraint names are required for SQLite batch migrations (ALTER TABLE emulation).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Rolling summary of messages that have aged out of the LLM context window (see app/agent/summarize.py).
    # `summarized_through_id` is the id of the last Message folded into `summary`, so each turn only needs to
    # summarize the newly-dropped slice instead of redoing the whole history.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summarized_through_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True once the conversation has read any external (fenced) content. It never goes back to False: the page may leave
    # the replayed window, but the summary or the agent's own replies can still carry what it said. A new chat is clean.
    tainted: Mapped[bool] = mapped_column(default=False, server_default="0")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    """One LLM-format message. `content` is a list of content blocks (text / tool_use / tool_result)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="todo", index=True)  # todo|in_progress|done|cancelled
    priority: Mapped[str] = mapped_column(String(8), default="medium")  # low|medium|high
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    postponement_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Phase 2: where the task sits in the goal -> milestone -> task and project -> task hierarchies.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True, index=True)
    milestone_id: Mapped[int | None] = mapped_column(
        ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    horizon: Mapped[str] = mapped_column(String(8), default="long")  # short | long
    category: Mapped[str] = mapped_column(String(16), default="other")  # career|education|research|skills|personal|other
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|paused|achieved|dropped
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    milestones: Mapped[list[Milestone]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", order_by="Milestone.position"
    )


class Milestone(Base):
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="todo")  # todo|in_progress|done
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    goal: Mapped[Goal] = relationship(back_populates="milestones")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active|paused|completed|archived
    priority: Mapped[str] = mapped_column(String(8), default="medium")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Commitment(Base):
    """Something the user promised to do ("I'll finish this by Friday").

    `proposed` = detected but not yet confirmed by the user. "Missed" is derived (open + deadline passed), not stored.
    """

    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(300))
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    importance: Mapped[str] = mapped_column(String(8), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # proposed|open|done|dropped
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    postponement_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Memory(Base):
    """A durable fact about the user that helps the agent help them (preferences, context, facts)."""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="fact", index=True)  # preference|context|fact
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(8), default="medium")
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PendingAction(Base):
    """A high-impact tool call waiting for explicit user approval."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_input: Mapped[Any] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|confirmed|rejected|failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """Append-only record of every tool call (allowed, queued, rejected or failed)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(16))  # agent | user
    tool_name: Mapped[str] = mapped_column(String(64))
    permission: Mapped[str] = mapped_column(String(16))
    params: Mapped[Any] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24))  # ok | error | pending_confirmation | rejected
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pending_action_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TaskEvent(Base):
    """Append-only history of what happened to a task. `Task.postponement_count` is just a cache of this.

    The event log is what lets the accountability layer say *when* and *why* something slipped. It survives
    deletion of the task (task_id becomes NULL, `task_title` keeps it readable).
    """

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    task_title: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(16), index=True)  # created|postponed|completed|reopened|cancelled|skipped
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Plan(Base):
    """The plan for one calendar day (in the user's timezone)."""

    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    capacity_minutes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    items: Mapped[list[PlanItem]] = relationship(
        back_populates="plan", cascade="all, delete-orphan", order_by="PlanItem.position"
    )


class PlanItem(Base):
    __tablename__ = "plan_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))  # snapshot, so the plan stays readable if the task is deleted
    planned_minutes: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_major: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(12), default="planned")  # planned|done|skipped

    plan: Mapped[Plan] = relationship(back_populates="items")


class WorkSession(Base):
    """Time actually spent working. This is the ground truth the accountability layer compares plans against."""

    __tablename__ = "work_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Reminder(Base):
    """A one-off reminder the user asked for. `fire_at` is naive UTC."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(String(300))
    fire_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)  # pending|sent|cancelled|failed
    linked_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # task|commitment|project|goal
    linked_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Checkin(Base):
    """A morning/evening/weekly check-in the agent initiated. One per (kind, day)."""

    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # morning|evening|weekly
    checkin_date: Mapped[date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text)  # the message that was sent
    user_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Notification(Base):
    """Every outbound message attempt. This table *is* the notification budget: counts and dedupe read from it."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # reminder|morning|evening|weekly|nudge
    priority: Mapped[str] = mapped_column(String(8))  # low|normal|high
    channel: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(8), default="sent")  # sent|failed
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Document(Base):
    """A file from the user's notes folder that has been indexed for search. `path` is relative to NOTES_DIR."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(500), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    mtime: Mapped[float] = mapped_column()
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    chunks: Mapped[list[Chunk]] = relationship(back_populates="document", cascade="all, delete-orphan", order_by="Chunk.position")


class Chunk(Base):
    """A paragraph-sized slice of a document. Its id doubles as the rowid in the `chunk_fts` FTS5 table."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="chunks")
