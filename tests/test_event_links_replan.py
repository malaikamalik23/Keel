"""Tasks linked to calendar events, and today's plan following the calendar when it changes."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.container import build_container
from app.db.models import Task
from app.integrations.notifier import LogNotifier
from app.security.permissions import PermissionPolicy
from app.tools import build_default_registry
from app.tools.base import ToolContext
from app.tools.executor import ToolExecutor
from app.tools.planning import get_plan
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM

TZ = ZoneInfo("Asia/Karachi")  # UTC+5
DAY = date(2026, 11, 18)


def at(h, m=0, day=DAY):
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def cal(*events: tuple[str, str, str, str], day: date = DAY) -> str:
    """events: (uid, local start HHMM, local end HHMM, title)."""
    body = ""
    for uid, s, e, title in events:
        su = f"{int(s[:2]) - 5:02d}{s[2:]}"
        eu = f"{int(e[:2]) - 5:02d}{e[2:]}"
        body += f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTART:{day:%Y%m%d}T{su}00Z\r\nDTEND:{day:%Y%m%d}T{eu}00Z\r\nSUMMARY:{title}\r\nEND:VEVENT\r\n"
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n{body}END:VCALENDAR\r\n"


class World:
    """One database; a calendar the test can change; tools and scheduler both reading it."""

    def __init__(self, ics: str):
        self.ics = ics
        self.notifier = LogNotifier()
        # Check-ins far from the test clock, so only the calendar step speaks.
        settings = make_settings(agent_timezone="Asia/Karachi", morning_checkin_time="03:00", evening_checkin_time="23:30")
        self.c = build_container(settings, llm=ScriptedLLM([]), notifier=self.notifier)
        self.c.scheduler.calendar_source = lambda: self.ics
        self.ex = ToolExecutor(build_default_registry(calendar_source=lambda: self.ics), PermissionPolicy())

    def run(self, name, args, now=at(8)):
        with self.c.session_factory() as db, patch.object(ToolContext, "now", lambda self_: now), \
                patch.object(ToolContext, "today", lambda self_: now.date()):
            out = self.ex.run(ToolContext(db=db, tz=TZ), name, args)
            db.commit()
            return out

    def tick(self, now):
        return self.c.scheduler.tick(now)


MEETING = ("mtg-1@cal", "1400", "1500", "Thesis committee https://evil.test")


def test_a_task_can_be_linked_to_a_real_event_only(make_container):
    w = World(cal(MEETING))
    out = w.run("create_task", {"title": "Prepare slides", "estimated_minutes": 60,
                                "before_event_uid": "mtg-1@cal", "before_event_start": "2026-11-18T14:00"})
    assert out.status == "ok" and out.result["task"]["due_date"] == "2026-11-18"
    assert out.result["task"]["before_event_at_utc"] == "2026-11-18T09:00"
    wrong = w.run("create_task", {"title": "x", "before_event_uid": "mtg-1@cal", "before_event_start": "2026-11-18T15:00"})
    assert wrong.status == "error" and "isn't on the calendar" in wrong.result["error"]
    half = w.run("create_task", {"title": "x", "before_event_uid": "mtg-1@cal"})
    assert half.status == "error" and "both" in half.result["error"]

    no_cal = make_container()
    with no_cal.session_factory() as db:
        res = no_cal.executor.run(ToolContext(db=db, tz=TZ), "create_task", {"title": "x", "before_event_uid": "a", "before_event_start": "2026-11-18T14:00"})
    assert res.status == "error" and "No calendar" in res.result["error"]


def test_a_linked_task_is_planned_to_finish_before_its_meeting():
    w = World(cal(("busy", "0900", "1300", "Lectures"), MEETING))
    w.run("create_task", {"title": "Prepare slides", "estimated_minutes": 45, "priority": "high",
                          "before_event_uid": "mtg-1@cal", "before_event_start": "2026-11-18T14:00"})
    w.run("create_task", {"title": "Read paper", "estimated_minutes": 30, "priority": "high"})
    plan = w.run("create_daily_plan", {"capacity_minutes": 300}).result
    blocks = {i["title"]: (i.get("start"), i.get("end")) for i in plan["items"]}
    assert blocks["Prepare slides"] == ("13:05", "13:50")  # the only time before 14:00 that is free
    assert blocks["Read paper"][0] >= "15:05"


def test_when_the_meeting_moves_the_task_follows_and_you_hear_about_it():
    w = World(cal(MEETING))
    tid = w.run("create_task", {"title": "Prepare slides", "before_event_uid": "mtg-1@cal",
                                "before_event_start": "2026-11-18T14:00"}).result["task"]["id"]
    w.ics = cal(("mtg-1@cal", "1100", "1200", "Thesis committee https://evil.test"), day=date(2026, 11, 20))
    w.tick(at(8))
    with w.c.session_factory() as db:
        t = db.get(Task, tid)
        assert t.due_date == date(2026, 11, 20) and t.event_start == datetime(2026, 11, 20, 6, 0)
        assert t.postponement_count == 0  # the meeting moved; the user didn't put anything off
    assert len(w.notifier.sent) == 1 and "Prepare slides" in w.notifier.sent[0] and "Fri Nov 20, 11:00" in w.notifier.sent[0]
    assert "evil" not in w.notifier.sent[0] and "Thesis committee" not in w.notifier.sent[0]

    w.ics = cal()  # cancelled
    w.tick(at(8, 20))  # within 30 minutes of the last check: the calendar isn't read again yet
    assert len(w.notifier.sent) == 1
    w.tick(at(8, 31))
    with w.c.session_factory() as db:
        assert db.get(Task, tid).event_uid is None
    assert len(w.notifier.sent) == 2 and "no longer on your calendar" in w.notifier.sent[1]


def test_a_new_meeting_moves_todays_blocks_that_have_not_started():
    w = World(cal())
    w.run("create_task", {"title": "Write FYP intro", "estimated_minutes": 60, "priority": "high"})
    w.run("create_task", {"title": "Read paper", "estimated_minutes": 30})
    plan = w.run("create_daily_plan", {"capacity_minutes": 240}).result
    assert {i["title"]: i["start"] for i in plan["items"]} == {"Write FYP intro": "09:00", "Read paper": "10:05"}

    w.ics = cal(("new", "1000", "1100", "Surprise meeting"))
    report = w.tick(at(9, 30))  # the 09:00 block is running: it stays; 10:05 now clashes
    with w.c.session_factory() as db:
        items = {i.title: (i.start_time, i.end_time) for i in get_plan(db, DAY).items}
    assert items["Write FYP intro"] == ("09:00", "10:00")
    assert items["Read paper"] == ("11:05", "11:35")
    assert report["replanned"] == 1 and "Read paper: 11:05-11:35" in w.notifier.sent[-1]
    assert "Surprise" not in json.dumps(w.notifier.sent)

    sent = len(w.notifier.sent)
    w.tick(at(10, 10))  # nothing new clashes: no change, no message
    assert len(w.notifier.sent) == sent


def test_without_a_calendar_the_step_does_nothing():
    w = World(cal())
    w.c.scheduler.calendar_source = None
    assert "replanned" not in w.tick(at(9))
    assert w.notifier.sent == []
    _ = timedelta  # keep the import used in case of edits
