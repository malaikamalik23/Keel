"""Morning / evening / weekly check-ins: facts from SQL, wording from the LLM (with a deterministic fallback)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Checkin, Commitment, Task, WorkSession
from app.services import accountability
from app.services.briefing import briefing_text, build_briefing
from app.services.notifications import parse_hhmm
from app.services.timeutil import window_utc

OPEN = ("todo", "in_progress")


def scheduled_time(kind: str, settings: Settings):
    return parse_hhmm(settings.morning_checkin_time if kind == "morning" else settings.evening_checkin_time)


def checkins_due(db: Session, now_local: datetime, settings: Settings) -> list[str]:
    """Kinds due right now: scheduled time has passed, still inside the grace window, and not yet sent today."""
    today = now_local.date()
    grace = timedelta(hours=settings.checkin_grace_hours)
    is_weekly_day = now_local.weekday() == settings.weekly_checkin_weekday
    candidates = ["morning", "weekly" if is_weekly_day else "evening"]  # weekly replaces evening on its day
    due = []
    for kind in candidates:
        start = datetime.combine(today, scheduled_time(kind, settings), tzinfo=now_local.tzinfo)
        if not (start <= now_local < start + grace):
            continue
        if db.scalars(select(Checkin).where(Checkin.kind == kind, Checkin.checkin_date == today)).first():
            continue
        due.append(kind)
    return due


def build_facts(db: Session, kind: str, today: date, tz: ZoneInfo) -> dict:
    from app.tools.planning import get_plan, plan_dict

    open_tasks = list(db.scalars(select(Task).where(Task.status.in_(OPEN))))
    plan = plan_dict(get_plan(db, today), today)
    facts: dict = {"kind": kind, "date": today.isoformat(), "plan": plan}

    if kind == "morning":
        # The daily briefing (own data only; calendar titles are added by code after the model has written its part).
        facts["briefing"] = build_briefing(db, today)
    elif kind == "evening":
        lo, hi = window_utc(today, today, tz)
        sessions = list(db.scalars(select(WorkSession).where(WorkSession.started_at >= lo, WorkSession.started_at < hi)))
        items = plan.get("items", [])
        facts["planned_done"] = [i["title"] for i in items if i["status"] == "done"]
        facts["planned_not_done"] = [i["title"] for i in items if i["status"] == "planned"]
        facts["skipped"] = [i["title"] for i in items if i["status"] == "skipped"]
        facts["minutes_worked"] = sum(s.minutes for s in sessions)
    else:  # weekly
        facts["report"] = accountability.accountability_report(db, today, tz, window_days=7)
        next_week = today + timedelta(days=7)
        facts["next_week"] = {
            "tasks_due": [{"title": t.title, "due": t.due_date.isoformat()} for t in sorted(open_tasks, key=lambda t: t.due_date or date.max)
                          if t.due_date and today < t.due_date <= next_week][:8],
            "promises_due": [{"text": c.text, "deadline": c.deadline.isoformat()}
                             for c in db.scalars(select(Commitment).where(Commitment.status == "open"))
                             if c.deadline and today < c.deadline <= next_week][:5],
        }
        facts["waiting_on"] = [{"title": t.title, "waiting_on": t.waiting_on} for t in open_tasks if t.waiting_on][:5]
        from app.tools.dates import upcoming

        facts["next_week"]["important_dates"] = [{"title": d["title"], "date": d["date"]} for d in upcoming(db, today, 7) if d["days_left"] > 0][:5]
    return facts


def fallback_text(facts: dict) -> str:
    """What gets sent if the LLM is unavailable. Plain, factual, useful."""
    kind = facts["kind"]
    if kind == "morning":
        return briefing_text(facts["briefing"])
    if kind == "evening":
        done, left = facts["planned_done"], facts["planned_not_done"]
        msg = f"Evening review: {len(done)} planned item(s) done"
        msg += f", {len(left)} not done ({', '.join(left[:3])}). What got in the way?" if left else ". Nice work."
        if facts["minutes_worked"]:
            msg += f" You logged {facts['minutes_worked']} minutes."
        return msg
    r = facts["report"]
    msg = f"Weekly review: {r['tasks']['completed']} tasks completed, {r['work']['total_minutes']} minutes logged."
    if r["most_postponed"]:
        msg += f" Most postponed: {r['most_postponed'][0]['title']} ({r['most_postponed'][0]['postponement_count']}×)."
    if r["neglected_goals"]:
        msg += f" No progress on: {r['neglected_goals'][0]['title']}."
    nxt = facts.get("next_week", {})
    due = [t["title"] for t in nxt.get("tasks_due", [])] + [p["text"] for p in nxt.get("promises_due", [])]
    if due:
        msg += " Due next week: " + ", ".join(due[:4]) + "."
    if facts.get("waiting_on"):
        msg += " Still waiting on: " + ", ".join(f"{w['waiting_on']} ({w['title']})" for w in facts["waiting_on"][:3]) + "."
    return msg + " What should next week's top priority be?"
