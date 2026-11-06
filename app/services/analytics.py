"""Longer-horizon analytics for the dashboard's Insights view. Pure SQL/Python over history; nothing is LLM-written."""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Plan, PlanItem, Task, TaskEvent, WorkSession
from app.services.timeutil import to_local_date, window_utc

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def analytics(db: Session, today: date, tz: ZoneInfo, *, weeks: int = 8, streak_days: int = 90) -> dict:
    first_week = _monday(today) - timedelta(weeks=weeks - 1)
    horizon_start = min(first_week, today - timedelta(days=streak_days - 1))
    lo, hi = window_utc(horizon_start, today, tz)

    events = list(db.scalars(select(TaskEvent).where(TaskEvent.at >= lo, TaskEvent.at < hi)))
    sessions = list(db.scalars(select(WorkSession).where(WorkSession.started_at >= lo, WorkSession.started_at < hi)))
    items = list(db.execute(select(PlanItem, Plan.plan_date).join(Plan).where(Plan.plan_date >= horizon_start, Plan.plan_date <= today)))

    # ---- weekly series ----
    series = []
    for i in range(weeks):
        start = first_week + timedelta(weeks=i)
        end = start + timedelta(days=6)
        ev = [e for e in events if start <= to_local_date(e.at, tz) <= end]
        its = [it for it, d in items if start <= d <= end]
        series.append({
            "week_start": start.isoformat(),
            "created": sum(e.type == "created" for e in ev),
            "completed": sum(e.type == "completed" for e in ev),
            "postponed": sum(e.type == "postponed" for e in ev),
            "planned_items": len(its), "planned_done": sum(i.status == "done" for i in its),
            "minutes_worked": sum(s.minutes for s in sessions if start <= to_local_date(s.started_at, tz) <= end),
        })

    # ---- streak: consecutive days (ending today, or yesterday if today is still empty) with real progress ----
    active_days = {to_local_date(e.at, tz) for e in events if e.type == "completed"} | {to_local_date(s.started_at, tz) for s in sessions}
    cursor = today if today in active_days else today - timedelta(days=1)
    current = 0
    while cursor in active_days:
        current += 1
        cursor -= timedelta(days=1)
    longest = run = 0
    for n in range(streak_days):
        run = run + 1 if (horizon_start + timedelta(days=n)) in active_days else 0
        longest = max(longest, run)

    # ---- commitments ----
    commitments = list(db.scalars(select(Commitment).where(Commitment.status.in_(("open", "done", "dropped")))))
    done = sum(c.status == "done" for c in commitments)
    dropped = sum(c.status == "dropped" for c in commitments)
    overdue = sum(c.status == "open" and c.deadline is not None and c.deadline < today for c in commitments)
    resolved = done + dropped + overdue

    # ---- estimates vs reality (completed tasks that have logged time) ----
    minutes_by_task: dict[int, int] = defaultdict(int)
    for s in db.scalars(select(WorkSession).where(WorkSession.task_id.is_not(None))):
        minutes_by_task[s.task_id] += s.minutes
    ratios = [
        minutes_by_task[t.id] / t.estimated_minutes
        for t in db.scalars(select(Task).where(Task.status == "done", Task.estimated_minutes.is_not(None)))
        if t.id in minutes_by_task
    ]

    # ---- postponement patterns ----
    postponed = [e for e in db.scalars(select(TaskEvent).where(TaskEvent.type == "postponed"))]
    reasons = Counter(e.reason.strip().lower() for e in postponed if e.reason and e.reason.strip())
    postponed_per_task = Counter(e.task_id for e in postponed if e.task_id is not None)
    completed_ids = {t.id for t in db.scalars(select(Task).where(Task.status == "done"))}

    weekday_counts = Counter(to_local_date(e.at, tz).weekday() for e in events if e.type == "completed")

    return {
        "weeks": series,
        "streak": {"current_days": current, "longest_days": longest, "window_days": streak_days},
        "commitments": {
            "done": done, "dropped": dropped, "overdue": overdue,
            "kept_rate": round(done / resolved, 2) if resolved else None,
        },
        "estimates": {
            "tasks_measured": len(ratios),
            "median_actual_vs_estimate": round(statistics.median(ratios), 2) if ratios else None,  # 1.5 = takes 50% longer
        },
        "postponement": {
            "total": len(postponed),
            "avg_before_completion": round(statistics.mean(postponed_per_task[i] for i in completed_ids), 2) if completed_ids else None,
            "top_reasons": [{"reason": r, "count": n} for r, n in reasons.most_common(5)],
        },
        "completions_by_weekday": {WEEKDAYS[d]: weekday_counts.get(d, 0) for d in range(7)},
    }
