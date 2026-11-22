"""Retention rules: how long Keel keeps each kind of history. Everything is kept forever unless you choose otherwise.

Rules are set on the Services tab (stored in `user_settings` as "retention:<category>" = days). They apply to logs and
history only, never to what you created yourself (tasks, goals, projects, promises, memories, applications, notes):
those you delete yourself. With the scheduler on they run once a day; "Delete now" on the dashboard runs them at once,
after showing how many rows each would remove.

Deletion uses SQLite's secure_delete, so removed rows are overwritten in the file rather than left in free pages.
Backups taken earlier still hold them until those backups are rotated out (BACKUP_KEEP).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    Checkin,
    Conversation,
    Message,
    Notification,
    PendingAction,
    TaskEvent,
    UserSetting,
    WorkflowRun,
    utcnow,
)


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    min_days: int  # shorter would break something (dedupe, budgets, accountability)
    note: str


CATEGORIES: dict[str, Category] = {c.id: c for c in (
    Category("conversations", "Chat conversations", 7, "Whole conversations with no message newer than this."),
    Category("approvals", "Finished approvals", 7, "Approved, rejected, failed and expired requests. Waiting ones are never removed."),
    Category("audit_log", "Audit log", 30, "The record of every tool call. It's your evidence of what Keel did; keep it at least as long as you'd want to check."),
    Category("notifications", "Sent messages log", 30, "Reminders, check-ins and nudges sent. Recent ones are kept so nothing is sent twice."),
    Category("checkins", "Check-ins and your replies", 30, "Morning, evening and weekly check-ins."),
    Category("workflow_runs", "Finished workflow runs", 30, "Done, failed and cancelled runs. Running or waiting ones are never removed."),
    Category("task_history", "Task history", 90, "Postponements and their reasons. Shorter history makes 'what have I been avoiding?' less complete."),
    Category("note_backups", "Old versions of edited notes", 7, "Copies kept in NOTES_DIR/.keel-backups when Keel edits a note."),
)}
PREFIX = "retention:"
LAST_RUN = "retention:last_run"
MAX_DAYS = 3650


def rules(db: Session) -> dict[str, int | None]:
    rows = {r.key: r.value for r in db.scalars(select(UserSetting).where(UserSetting.key.like(f"{PREFIX}%")))}
    return {c: (rows.get(PREFIX + c) if isinstance(rows.get(PREFIX + c), int) else None) for c in CATEGORIES}


def set_rule(db: Session, category: str, days: int | None) -> None:
    cat = CATEGORIES.get(category)
    if cat is None:
        raise ValueError(f"Unknown category '{category}'.")
    if days is not None and not (cat.min_days <= days <= MAX_DAYS):
        raise ValueError(f"{cat.label}: keep at least {cat.min_days} days (or forever).")
    key = PREFIX + category
    row = db.get(UserSetting, key)
    if row is None:
        db.add(UserSetting(key=key, value=days, updated_at=utcnow()))
    else:
        row.value, row.updated_at = days, utcnow()
    db.commit()


def _old_conversations(db: Session, cutoff: datetime):
    last = (select(Message.conversation_id, func.max(Message.created_at).label("last"))
            .group_by(Message.conversation_id).subquery())
    return (select(Conversation.id).outerjoin(last, last.c.conversation_id == Conversation.id)
            .where(func.coalesce(last.c.last, Conversation.created_at) < cutoff))


def _where(category: str, cutoff: datetime):
    """(model, condition) for the table-backed categories."""
    finished = ("done", "failed", "cancelled")
    return {
        "approvals": (PendingAction, and_(PendingAction.status != "pending", func.coalesce(PendingAction.resolved_at, PendingAction.created_at) < cutoff)),
        "audit_log": (AuditLog, AuditLog.created_at < cutoff),
        "notifications": (Notification, Notification.sent_at < cutoff),
        "checkins": (Checkin, Checkin.created_at < cutoff),
        "workflow_runs": (WorkflowRun, and_(WorkflowRun.status.in_(finished), func.coalesce(WorkflowRun.finished_at, WorkflowRun.updated_at) < cutoff)),
        "task_history": (TaskEvent, TaskEvent.at < cutoff),
    }[category]


def _note_backups(notes_dir: str | None, cutoff: datetime) -> list[Path]:
    """Timestamped folders in NOTES_DIR/.keel-backups older than the cutoff (named YYYYMMDD-HHMMSS, see tools/files.py)."""
    if not notes_dir:
        return []
    root = Path(notes_dir).expanduser() / ".keel-backups"
    if not root.is_dir() or root.is_symlink():
        return []
    out = []
    for d in root.iterdir():
        try:
            made = datetime.strptime(d.name[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            continue
        if d.is_dir() and not d.is_symlink() and made < cutoff:
            out.append(d)
    return sorted(out)


def apply(db: Session, *, notes_dir: str | None = None, now: datetime | None = None, dry_run: bool = False,
          only: dict[str, int | None] | None = None) -> dict[str, int]:
    """Delete what the rules say has expired. Returns rows (or backup folders) removed per category; with dry_run,
    what would be. `only` previews rules not saved yet."""
    now = now or utcnow()
    active = only if only is not None else rules(db)
    counts: dict[str, int] = {}
    if not dry_run and db.get_bind().dialect.name == "sqlite":
        db.execute(text("PRAGMA secure_delete=ON"))
    for cat, days in active.items():
        if days is None or cat not in CATEGORIES:
            continue
        cutoff = now - timedelta(days=days)
        if cat == "note_backups":
            folders = _note_backups(notes_dir, cutoff)
            counts[cat] = len(folders)
            if not dry_run:
                for d in folders:
                    shutil.rmtree(d)
            continue
        if cat == "conversations":
            ids = list(db.scalars(_old_conversations(db, cutoff)))
            # A request still waiting for approval keeps its conversation, so it can still be answered.
            waiting = set(db.scalars(select(PendingAction.conversation_id).where(
                PendingAction.conversation_id.in_(ids), PendingAction.status == "pending")))
            ids = [i for i in ids if i not in waiting]
            counts[cat] = len(ids)
            if ids and not dry_run:
                db.execute(update(PendingAction).where(PendingAction.conversation_id.in_(ids)).values(conversation_id=None))
                db.execute(delete(Message).where(Message.conversation_id.in_(ids)))
                db.execute(delete(Conversation).where(Conversation.id.in_(ids)))
            continue
        model, cond = _where(cat, cutoff)
        counts[cat] = db.scalar(select(func.count()).select_from(model).where(cond)) or 0
        if counts[cat] and not dry_run:
            db.execute(delete(model).where(cond))
    if not dry_run:
        row = db.get(UserSetting, LAST_RUN)
        stamp = now.isoformat(timespec="seconds")
        if row is None:
            db.add(UserSetting(key=LAST_RUN, value=stamp, updated_at=now))
        else:
            row.value, row.updated_at = stamp, now
        db.commit()
    return counts


def last_run(db: Session) -> str | None:
    row = db.get(UserSetting, LAST_RUN)
    return row.value if row else None
