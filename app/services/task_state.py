"""Which open tasks can actually be worked on now.

A task stays *open* (todo / in_progress) while it waits, so lists, counts and analytics keep seeing it. It is not
*actionable* while it waits on someone else (`waiting_on`) or on another task that isn't finished yet (`blocked_by`).
The planner and the nudges use this; nothing here changes data.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import Task

OPEN = ("todo", "in_progress")
RECURRENCES = ("daily", "weekdays", "weekly", "monthly")


def open_blocker(db: Session, task: Task) -> Task | None:
    """The unfinished task this one waits for, or None (no blocker, or the blocker is done/cancelled/deleted)."""
    if task.blocked_by_id is None:
        return None
    blocker = db.get(Task, task.blocked_by_id)
    return blocker if blocker is not None and blocker.status in OPEN else None


def actionable(db: Session, task: Task) -> bool:
    return task.status in OPEN and not task.waiting_on and open_blocker(db, task) is None


def would_cycle(db: Session, task_id: int, blocker_id: int) -> bool:
    """True if making `task_id` wait on `blocker_id` closes a loop (A waits on B waits on ... A)."""
    seen, current = set(), blocker_id
    while current is not None and current not in seen:
        if current == task_id:
            return True
        seen.add(current)
        nxt = db.get(Task, current)
        current = nxt.blocked_by_id if nxt is not None else None
    return False


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    for day in (d.day, 30, 29, 28):  # 31 Jan + 1 month -> 28/29 Feb
        try:
            return date(year, month, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def next_due(rule: str, after: date, today: date) -> date:
    """The next due date for a repeating task: one step after `after`, then on until it is later than today."""
    def step(d: date) -> date:
        if rule == "daily":
            return d + timedelta(days=1)
        if rule == "weekdays":
            d += timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            return d
        if rule == "weekly":
            return d + timedelta(days=7)
        if rule == "monthly":
            return _add_months(d, 1)
        raise ValueError(f"Unknown recurrence {rule!r}")

    d = step(after)
    while d <= today:
        d = step(d)
    return d
