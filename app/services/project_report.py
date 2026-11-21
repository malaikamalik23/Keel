"""A project's progress report, assembled by code from the database: only facts, no judgement.

Used by the `project_report` tool (the model words it) and the dashboard (shown as is). Counts and lists come from
tasks, work sessions and the project log, so a report can never claim progress that isn't recorded.
"""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import false, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Project, ProjectEntry, ProjectLink, Task, WorkSession
from app.services.timeutil import to_local_date, window_utc

OPEN = ("todo", "in_progress")


def entry_dict(e: ProjectEntry, tz: ZoneInfo) -> dict:
    d = {"id": e.id, "kind": e.kind, "text": e.text, "date": to_local_date(e.created_at, tz).isoformat()}
    if e.rationale:
        d["why"] = e.rationale
    if e.kind == "blocker":
        d["resolved"] = e.resolved_at is not None
        if e.resolution:
            d["resolution"] = e.resolution
    return d


def link_dict(link: ProjectLink) -> dict:
    return {"id": link.id, "kind": link.kind, "target": link.target, "title": link.title}


def open_blockers(db: Session, project_id: int | None = None) -> list[ProjectEntry]:
    stmt = select(ProjectEntry).where(ProjectEntry.kind == "blocker", ProjectEntry.resolved_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(ProjectEntry.project_id == project_id)
    return list(db.scalars(stmt.order_by(ProjectEntry.created_at)))


def task_progress(db: Session, project_id: int) -> dict:
    rows = dict(db.execute(
        select(Task.status, func.count()).where(Task.project_id == project_id, Task.status != "cancelled").group_by(Task.status)
    ).all())
    total = sum(rows.values())
    done = rows.get("done", 0)
    return {"tasks_total": total, "done": done, "open": total - done, "percent": round(100 * done / total) if total else None}


def build_report(db: Session, project: Project, today: date, tz: ZoneInfo, days: int = 7) -> dict:
    start, end = window_utc(today - timedelta(days=days - 1), today, tz)
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id, Task.status != "cancelled")))
    by_id = {t.id: t for t in tasks}
    open_tasks = [t for t in tasks if t.status in OPEN]

    stuck = []
    for t in open_tasks:
        blocker = by_id.get(t.blocked_by_id) or (db.get(Task, t.blocked_by_id) if t.blocked_by_id else None)
        if blocker is not None and blocker.status in OPEN:
            stuck.append({"id": t.id, "title": t.title, "waiting_for_task": blocker.title})
        elif t.waiting_on:
            stuck.append({"id": t.id, "title": t.title, "waiting_on": t.waiting_on})

    task_ids = [t.id for t in tasks]
    minutes = db.scalar(select(func.coalesce(func.sum(WorkSession.minutes), 0)).where(
        WorkSession.started_at >= start, WorkSession.started_at < end,
        or_(WorkSession.project_id == project.id, WorkSession.task_id.in_(task_ids) if task_ids else false()),
    ))
    entries = list(db.scalars(select(ProjectEntry).where(ProjectEntry.project_id == project.id).order_by(ProjectEntry.created_at)))
    in_period = [e for e in entries if start <= e.created_at < end]

    report = {
        "project": {"id": project.id, "name": project.name, "status": project.status, "purpose": project.purpose,
                    "deadline": project.deadline.isoformat() if project.deadline else None, "repo": project.repo},
        "period": {"from": (today - timedelta(days=days - 1)).isoformat(), "to": today.isoformat()},
        "progress": task_progress(db, project.id),
        "done_in_period": [t.title for t in tasks if t.status == "done" and t.completed_at and start <= t.completed_at < end],
        "overdue": [{"id": t.id, "title": t.title, "due": t.due_date.isoformat()} for t in open_tasks if t.due_date and t.due_date < today],
        "stuck": stuck,
        "minutes_worked": int(minutes or 0),
        "open_blockers": [{**entry_dict(e, tz), "days_open": (today - to_local_date(e.created_at, tz)).days}
                          for e in entries if e.kind == "blocker" and e.resolved_at is None],
        "resolved_blockers": [entry_dict(e, tz) for e in entries if e.kind == "blocker" and e.resolved_at and start <= e.resolved_at < end],
        "decisions": [entry_dict(e, tz) for e in entries if e.kind == "decision"][-10:],
        "updates": [entry_dict(e, tz) for e in in_period if e.kind == "update"],
        "links": [link_dict(x) for x in db.scalars(select(ProjectLink).where(ProjectLink.project_id == project.id).order_by(ProjectLink.id))],
    }
    if project.deadline:
        report["days_to_deadline"] = (project.deadline - today).days
    if project.repo:
        report["github_hint"] = f"Open pull requests and commits for {project.repo}: call github_list_pulls / github_recent_commits."
    return report
