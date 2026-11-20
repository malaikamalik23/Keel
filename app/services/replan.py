"""Keep today's time blocks honest when the calendar changes: if a meeting now overlaps a block that hasn't started,
re-fit the blocks that haven't started into the free time that is left. Finished, skipped and running blocks stay.

Only event times are used. Nothing is dropped: a block that no longer fits stays in the plan without a time.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Plan, Task
from app.services.scheduling import GAP_MINUTES, Interval, free_slots, place


def _block(day, item, tz: ZoneInfo) -> Interval | None:
    if not item.start_time or not item.end_time:
        return None
    h1, m1 = map(int, item.start_time.split(":"))
    h2, m2 = map(int, item.end_time.split(":"))
    return Interval(datetime(day.year, day.month, day.day, h1, m1, tzinfo=tz), datetime(day.year, day.month, day.day, h2, m2, tzinfo=tz))


def refit_today(db: Session, plan: Plan | None, busy: list[Interval], now: datetime, tz: ZoneInfo, workday) -> list[dict]:
    """Re-fit today's not-yet-started blocks if any of them now clashes with a meeting. Returns what moved."""
    if plan is None:
        return []
    day = plan.plan_date
    gap = timedelta(minutes=GAP_MINUTES)
    upcoming = [i for i in plan.items if i.status == "planned" and (b := _block(day, i, tz)) is not None and b.start >= now]
    clash = any(b.start < m.end + gap and m.start - gap < b.end for i in upcoming if (b := _block(day, i, tz)) for m in busy)
    if not clash:
        return []
    # Time already promised to blocks that are running now must not be handed out again.
    running = [b for i in plan.items if i.status == "planned" and (b := _block(day, i, tz)) and b.start < now < b.end]
    slots = free_slots(day, tz, workday, sorted(busy + running, key=lambda iv: iv.start), now=now)
    order = sorted(upcoming, key=lambda i: i.position)
    end_by = {}
    for i in order:
        task = db.get(Task, i.task_id) if i.task_id else None
        if task is not None and task.event_start:
            from app.services.event_links import from_utc_naive

            start = from_utc_naive(task.event_start, tz)
            end_by[i.id] = start if start.date() == day else None
    placed, _ = place([(i.id, i.planned_minutes, end_by.get(i.id)) for i in order], slots)
    moved = []
    for i in order:
        old = (i.start_time, i.end_time)
        new = placed.get(i.id)
        i.start_time, i.end_time = (new.start.strftime("%H:%M"), new.end.strftime("%H:%M")) if new else (None, None)
        if (i.start_time, i.end_time) != old:
            moved.append({"title": i.title, "was": f"{old[0]}-{old[1]}", "now": f"{i.start_time}-{i.end_time}" if new else None})
    db.flush()
    return moved
