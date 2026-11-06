"""Behavioural analytics computed from the event history. SQL decides *what is true*; the LLM only words it.

Nothing here is written by a model: postponement counts, completion rates and neglected goals are aggregates
over task_events / work_sessions / plan_items, so the accountability layer can't hallucinate a pattern.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Commitment, Goal, Plan, PlanItem, Project, Task, TaskEvent, WorkSession, utcnow,
)
from app.services.timeutil import to_local_date, window_utc

OPEN = ("todo", "in_progress")


def _age_days(created: datetime, now: datetime) -> int:
    return max((now - created).days, 0)


def accountability_report(
    db: Session, today: date, tz: ZoneInfo, *, window_days: int = 7, neglect_days: int = 14, avoided_min_age_days: int = 7
) -> dict:
    start = today - timedelta(days=window_days - 1)
    w_start, w_end = window_utc(start, today, tz)
    now = utcnow()

    # ---- throughput ----
    created = db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.type == "created", TaskEvent.at >= w_start, TaskEvent.at < w_end)) or 0
    completed = db.scalar(select(func.count()).select_from(TaskEvent).where(TaskEvent.type == "completed", TaskEvent.at >= w_start, TaskEvent.at < w_end)) or 0
    open_tasks = list(db.scalars(select(Task).where(Task.status.in_(OPEN))))
    overdue_tasks = [t for t in open_tasks if t.due_date and t.due_date < today]

    # ---- plan adherence ----
    items = list(db.scalars(select(PlanItem).join(Plan).where(Plan.plan_date >= start, Plan.plan_date <= today)))
    counted = items
    planned_n = len(counted)
    done_n = sum(i.status == "done" for i in counted)
    skipped_n = sum(i.status == "skipped" for i in counted)

    # ---- postponement pattern ----
    postponed_events = list(db.scalars(select(TaskEvent).where(TaskEvent.type == "postponed", TaskEvent.at >= w_start, TaskEvent.at < w_end).order_by(TaskEvent.at)))
    by_task: dict[int | None, list[TaskEvent]] = defaultdict(list)
    for ev in postponed_events:
        by_task[ev.task_id].append(ev)
    most_postponed = []
    for t in sorted((t for t in open_tasks if t.postponement_count > 0), key=lambda t: (-t.postponement_count, t.id))[:5]:
        recent = by_task.get(t.id, [])
        most_postponed.append({
            "task_id": t.id, "title": t.title, "postponement_count": t.postponement_count,
            "postponed_in_window": len(recent),
            "reasons_given": [e.reason for e in recent if e.reason][-3:],
            "asked_why": any(e.reason for e in recent) if recent else None,
        })

    # ---- avoidance: old, open, never worked on ----
    worked_task_ids = set(db.scalars(select(WorkSession.task_id).where(WorkSession.task_id.is_not(None))))
    avoided = [
        {"task_id": t.id, "title": t.title, "age_days": _age_days(t.created_at, now), "postponement_count": t.postponement_count, "priority": t.priority}
        for t in open_tasks
        if _age_days(t.created_at, now) >= avoided_min_age_days and t.id not in worked_task_ids
    ]
    avoided.sort(key=lambda a: (-a["postponement_count"], -a["age_days"], a["task_id"]))

    # ---- commitments ----
    overdue_commitments = [
        {"commitment_id": c.id, "text": c.text, "deadline": c.deadline.isoformat(), "postponement_count": c.postponement_count}
        for c in db.scalars(select(Commitment).where(Commitment.status == "open"))
        if c.deadline and c.deadline < today
    ]
    kept = db.scalar(select(func.count()).select_from(Commitment).where(Commitment.status == "done", Commitment.completed_at >= w_start, Commitment.completed_at < w_end)) or 0

    # ---- time actually spent ----
    sessions = list(db.scalars(select(WorkSession).where(WorkSession.started_at >= w_start, WorkSession.started_at < w_end)))
    project_names = {p.id: p.name for p in db.scalars(select(Project))}
    minutes_by_project: dict[str, int] = defaultdict(int)
    for s in sessions:
        minutes_by_project[project_names.get(s.project_id, "(no project)")] += s.minutes

    return {
        "window": {"start": start.isoformat(), "end": today.isoformat(), "days": window_days},
        "tasks": {
            "created": created, "completed": completed, "open_now": len(open_tasks), "overdue": len(overdue_tasks),
            "overdue_titles": [t.title for t in overdue_tasks][:5],
        },
        "plan_adherence": {
            "planned_items": planned_n, "done": done_n, "skipped": skipped_n,
            "rate": round(done_n / planned_n, 2) if planned_n else None,
        },
        "most_postponed": most_postponed,
        "avoided_tasks": avoided[:5],
        "overdue_commitments": overdue_commitments[:5],
        "commitments_kept_in_window": kept,
        "neglected_goals": neglected_goals(db, today, tz, neglect_days=neglect_days),
        "work": {"total_minutes": sum(s.minutes for s in sessions), "sessions": len(sessions), "minutes_by_project": dict(minutes_by_project)},
    }


def neglected_goals(db: Session, today: date, tz: ZoneInfo, *, neglect_days: int = 14) -> list[dict]:
    """Active goals with no activity (task completed/created, or work logged on one of their tasks) recently."""
    now = utcnow()
    cutoff = now - timedelta(days=neglect_days)
    out = []
    for g in db.scalars(select(Goal).where(Goal.status == "active")):
        task_ids = list(db.scalars(select(Task.id).where(Task.goal_id == g.id)))
        last: datetime | None = None
        if task_ids:
            cand = [
                db.scalar(select(func.max(TaskEvent.at)).where(TaskEvent.task_id.in_(task_ids), TaskEvent.type.in_(("created", "completed")))),
                db.scalar(select(func.max(WorkSession.started_at)).where(WorkSession.task_id.in_(task_ids))),
            ]
            cand = [c for c in cand if c]
            last = max(cand) if cand else None
        reference = last or g.created_at
        if reference < cutoff:
            out.append({
                "goal_id": g.id, "title": g.title, "priority": g.priority,
                "days_since_activity": _age_days(reference, now), "ever_had_activity": last is not None,
            })
    out.sort(key=lambda r: -r["days_since_activity"])
    return out


def task_history(db: Session, task_id: int) -> list[dict]:
    rows = db.scalars(select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.at, TaskEvent.id))
    return [
        {"type": e.type, "at": e.at.isoformat(timespec="minutes"), "old": e.old_value, "new": e.new_value, "reason": e.reason}
        for e in rows
    ]
