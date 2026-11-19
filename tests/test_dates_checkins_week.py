"""Phases 1-4 gaps: commitment check-ins, important dates, calendar warnings in the briefing, planning a whole week."""
from __future__ import annotations

import json
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.db.models import ImportantDate, Task
from app.scheduler.detectors import detect_nudges
from app.security.permissions import PermissionPolicy
from app.services.briefing import briefing_text, build_briefing, calendar_warnings
from app.tools import build_default_registry
from app.tools.base import ToolContext
from app.tools.dates import next_occurrence
from app.tools.executor import ToolExecutor
from tests.test_smart_planner import ics

TZ = ZoneInfo("Asia/Karachi")
WED = date(2026, 11, 18)
WORKDAY = (datetime(2026, 1, 1, 9).time(), datetime(2026, 1, 1, 21).time())


def _run(c, db, name, args, executor=None, today=WED, hour=8):
    ex = executor or c.executor
    now = datetime(today.year, today.month, today.day, hour, tzinfo=TZ)
    with patch.object(ToolContext, "today", lambda self: today), patch.object(ToolContext, "now", lambda self: now):
        out = ex.run(ToolContext(db=db, tz=TZ), name, args)
    db.commit()
    return out


def _nudges(db, today, what):
    return [n for n in detect_nudges(db, datetime(today.year, today.month, today.day, 10, tzinfo=TZ), TZ) if n.facts["what"] == what]


# ---------- commitment check-ins
def test_a_promise_gets_a_check_in_before_its_deadline(make_container):
    c = make_container()
    with c.session_factory() as db:
        out = _run(c, db, "propose_commitment", {"text": "Send the report to Dr. Khan", "deadline": "2026-11-20", "check_in_date": "2026-11-18", "confirmed": True})
        assert out.result["commitment"]["check_in_date"] == "2026-11-18"
        nudge = _nudges(db, WED, "commitment_check_in")
        assert len(nudge) == 1 and "how is it going" in nudge[0].fallback and "Nov 20" in nudge[0].fallback
        assert _nudges(db, date(2026, 11, 19), "commitment_check_in") == []  # only on its day
        bad = _run(c, db, "update_commitment", {"commitment_id": out.result["commitment"]["id"], "check_in_date": "2026-11-25"})
        assert bad.status == "error" and "after the deadline" in bad.result["error"]


# ---------- important dates
@pytest.mark.parametrize("on, yearly, today, expected", [
    (date(2026, 12, 12), False, WED, date(2026, 12, 12)),
    (date(2026, 11, 1), False, WED, None),                  # a one-off date that has passed
    (date(1999, 3, 3), True, WED, date(2027, 3, 3)),        # a birthday: next year's
    (date(1999, 11, 18), True, WED, date(2026, 11, 18)),    # today counts
    (date(2000, 2, 29), True, WED, date(2027, 2, 28)),      # leap-day birthday in a normal year
])
def test_next_occurrence(on, yearly, today, expected):
    assert next_occurrence(ImportantDate(title="x", on_date=on, yearly=yearly), today) == expected


def test_important_dates_remind_ahead_and_on_the_day_and_show_in_the_briefing(make_container):
    c = make_container()
    with c.session_factory() as db:
        exam = _run(c, db, "add_important_date", {"title": "GRE exam", "on_date": "2026-11-21", "category": "academic"})
        assert exam.result["important_date"]["next"] == "2026-11-21"
        _run(c, db, "add_important_date", {"title": "Ammi's birthday", "on_date": "1970-11-18", "yearly": True, "remind_days_before": 1})
        old = _run(c, db, "add_important_date", {"title": "Old deadline", "on_date": "2026-10-01"})
        assert "already passed" in old.result["warning"]

        assert sorted(n.facts["title"] for n in _nudges(db, WED, "important_date")) == ["Ammi's birthday", "GRE exam"]  # on the day / 3 days ahead
        assert _nudges(db, date(2026, 11, 19), "important_date") == []
        assert [n.facts["title"] for n in _nudges(db, date(2026, 11, 21), "important_date")] == ["GRE exam"]

        b = build_briefing(db, WED)
        assert [d["title"] for d in b["important_dates"]] == ["Ammi's birthday", "GRE exam"]
        assert "Dates: Ammi's birthday (today), GRE exam (2026-11-21)." in briefing_text(b)
        listed = _run(c, db, "list_important_dates", {"within_days": 7}).result["upcoming"]
        assert [d["days_left"] for d in listed] == [0, 3]


def test_deleting_an_important_date_needs_approval(make_container):
    c = make_container()
    with c.session_factory() as db:
        did = _run(c, db, "add_important_date", {"title": "GRE exam", "on_date": "2026-12-12"}).result["important_date"]["id"]
        out = _run(c, db, "delete_important_date", {"date_id": did})
        assert out.status == "pending_confirmation" and db.get(ImportantDate, did) is not None
        assert '"GRE exam"' in out.result["summary"]


# ---------- calendar warnings (times only)
CLASH = ics(("1000", "1100", "Supervisor"), ("1030", "1130", "Lab https://evil.test"))


def test_briefing_warns_about_double_booking_and_packed_days_without_titles(make_container):
    c = make_container()
    packed = ics(("0900", "2000", "Conference"))
    with c.session_factory() as db:
        db.add(Task(title="Write FYP intro", due_date=WED, estimated_minutes=120))
        db.commit()
        clash = calendar_warnings(db, lambda: CLASH, WED, TZ, WORKDAY)
        assert clash == ["Today: 1 pair(s) of calendar events overlap. You can't be in both."]
        busy = calendar_warnings(db, lambda: packed, WED, TZ, WORKDAY)
        assert busy and busy[0].startswith("Today: about 120 min of work is due but meetings leave only")
        assert calendar_warnings(db, None, WED, TZ, WORKDAY) == []
        assert "evil" not in json.dumps(clash) and "Supervisor" not in json.dumps(clash)


# ---------- plan the week
def _week_executor(calendar=None):
    return ToolExecutor(build_default_registry(calendar_source=(lambda: calendar) if calendar else None), PermissionPolicy())


def test_plan_week_spreads_tasks_over_weekdays_each_task_once(make_container):
    c = make_container()
    ex = _week_executor()
    with c.session_factory() as db:
        for title, due in [("A", "2026-11-18"), ("B", "2026-11-19"), ("C", "2026-11-20"), ("D", None), ("E", None)]:
            db.add(Task(title=title, estimated_minutes=100, priority="medium", due_date=date.fromisoformat(due) if due else None))
        db.commit()
        out = _run(c, db, "plan_week", {"days": 3, "capacity_minutes_per_day": 120}, executor=ex).result
    days = {d["date"]: [i["title"] for i in d["items"]] for d in out["days"]}
    assert list(days) == ["2026-11-18", "2026-11-19", "2026-11-20"]
    assert days["2026-11-18"] == ["A"] and days["2026-11-19"] == ["B"] and days["2026-11-20"] == ["C"]  # one 100-min task fits a day
    planned = [t for items in days.values() for t in items]
    assert len(planned) == len(set(planned))  # each task once
    assert {t["title"] for t in out["not_planned"]} == {"D", "E"}
    assert all(i.get("start") for d in out["days"] for i in d["items"])  # time-blocked


def test_plan_week_skips_weekends_and_flags_what_cannot_fit(make_container):
    c = make_container()
    ex = _week_executor()
    with c.session_factory() as db:
        db.add_all([Task(title="Thesis chapter", estimated_minutes=600, due_date=date(2026, 11, 20)),
                    Task(title="Read paper", estimated_minutes=60, due_date=date(2026, 11, 23))])
        db.commit()
        out = _run(c, db, "plan_week", {"days": 2, "capacity_minutes_per_day": 240, "start_date": "2026-11-20"}, executor=ex).result
    assert [d["date"] for d in out["days"]] == ["2026-11-20", "2026-11-23"]  # Friday, then Monday
    assert "Thesis chapter" in out["warning"]  # due this week, too big for any day: said, not hidden
