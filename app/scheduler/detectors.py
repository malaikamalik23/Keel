"""Deterministic detectors: SQL decides *whether* something is worth a message; the LLM only words it.

Each detector returns Nudges with a stable `dedupe_key`, so the same problem is not announced twice
(e.g. an overdue task nags at most once per day, a slipping task only again when it slips further).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Plan, Task, WorkSession
from app.services.accountability import neglected_goals
from app.services.timeutil import window_utc

OPEN = ("todo", "in_progress")
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


@dataclass
class Nudge:
    priority: str
    dedupe_key: str
    facts: dict
    fallback: str
    kind: str = "nudge"


def detect_nudges(db: Session, now_local: datetime, tz: ZoneInfo) -> list[Nudge]:
    today = now_local.date()
    out: list[Nudge] = []

    # Overdue commitments (the user promised these): high importance -> high priority.
    for c in db.scalars(select(Commitment).where(Commitment.status == "open")):
        if c.deadline and c.deadline < today:
            out.append(Nudge(
                "high" if c.importance == "high" else "normal", f"commitment-overdue:{c.id}:{today}",
                {"what": "overdue_commitment", "text": c.text, "deadline": c.deadline.isoformat(), "postponed": c.postponement_count},
                f"You committed to “{c.text}” by {c.deadline:%b %d}. Still doable today, or should we reset the deadline?",
            ))

    overdue = [t for t in db.scalars(select(Task).where(Task.status.in_(OPEN))) if t.due_date and t.due_date < today]
    if overdue:
        titles = [t.title for t in overdue][:3]
        out.append(Nudge(
            "normal", f"overdue-tasks:{today}", {"what": "overdue_tasks", "count": len(overdue), "titles": titles},
            f"{len(overdue)} task(s) are overdue: {', '.join(titles)}. Pick one and do 25 minutes on it?",
        ))

    # Things that keep slipping: re-fire only when the count goes up.
    for t in db.scalars(select(Task).where(Task.status.in_(OPEN), Task.postponement_count >= 3)):
        out.append(Nudge(
            "normal", f"slipping:{t.id}:{t.postponement_count}",
            {"what": "slipping_task", "title": t.title, "postponed": t.postponement_count},
            f"“{t.title}” has slipped {t.postponement_count} times. What's making it hard — too big, unclear, or not important anymore?",
        ))

    # Plan made but nothing started by mid-afternoon.
    plan = db.scalars(select(Plan).where(Plan.plan_date == today)).first()
    if plan and plan.items and now_local.hour >= 14:
        lo, hi = window_utc(today, today, tz)
        worked = db.scalars(select(WorkSession).where(WorkSession.started_at >= lo, WorkSession.started_at < hi)).first()
        if not worked and not any(i.status == "done" for i in plan.items):
            first = next((i for i in plan.items if i.status == "planned"), None)
            if first:
                out.append(Nudge(
                    "normal", f"plan-stalled:{today}", {"what": "plan_stalled", "first_item": first.title, "minutes": first.planned_minutes},
                    f"Nothing from today's plan is done yet. Start with “{first.title}” — just the first 25 minutes?",
                ))

    iso = today.isocalendar()
    for g in neglected_goals(db, today, tz)[:2]:
        out.append(Nudge(
            "low", f"neglected:{g['goal_id']}:{iso.year}-W{iso.week}",
            {"what": "neglected_goal", "title": g["title"], "days": g["days_since_activity"]},
            f"No progress on “{g['title']}” for {g['days_since_activity']} days. Want one small task for it this week?",
        ))

    out.sort(key=lambda n: (PRIORITY_ORDER[n.priority], n.dedupe_key))
    return out
