from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import Task, TaskEvent

EVENT_TYPES = ("created", "postponed", "completed", "reopened", "cancelled", "skipped")


def record_task_event(
    db: Session, task: Task, type_: str, *, old: str | None = None, new: str | None = None, reason: str | None = None
) -> TaskEvent:
    """Append an event. The caller commits (events land atomically with the change they describe)."""
    if type_ not in EVENT_TYPES:  # a real check, not an assert: asserts vanish under `python -O`
        raise ValueError(f"Unknown task event type: {type_!r}")
    ev = TaskEvent(task_id=task.id, task_title=task.title, type=type_, old_value=old, new_value=new, reason=reason)
    db.add(ev)
    return ev
