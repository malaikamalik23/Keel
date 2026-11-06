from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.models import Commitment, Plan, PlanItem, Task, TaskEvent, WorkSession
from app.services.analytics import analytics
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM

TZ = ZoneInfo("UTC")
TODAY = date(2026, 11, 11)  # Wednesday; this week's Monday is Nov 9


def at(d: date, hour=12):
    return datetime(d.year, d.month, d.day, hour)


def seed(db):
    t_done = Task(title="done", status="done", estimated_minutes=60)
    t_open = Task(title="open")
    db.add_all([t_done, t_open])
    db.flush()
    ev = lambda t, typ, d, **kw: db.add(TaskEvent(task_id=t.id, task_title=t.title, type=typ, at=at(d), **kw))  # noqa: E731
    ev(t_done, "created", date(2026, 11, 2)); ev(t_done, "postponed", date(2026, 11, 4), reason="Too big"); ev(t_done, "postponed", date(2026, 11, 6), reason="too big")
    ev(t_done, "completed", date(2026, 11, 10)); ev(t_open, "created", date(2026, 11, 10))
    db.add(WorkSession(task_id=t_done.id, started_at=at(date(2026, 11, 9)), minutes=90))
    db.add(WorkSession(task_id=t_done.id, started_at=at(date(2026, 11, 11)), minutes=30))
    plan = Plan(plan_date=date(2026, 11, 10), capacity_minutes=240)
    plan.items = [PlanItem(task_id=t_done.id, title="done", planned_minutes=60, status="done"), PlanItem(task_id=t_open.id, title="open", planned_minutes=30, status="skipped", position=1)]
    db.add(plan)
    db.add_all([
        Commitment(text="kept", status="done"), Commitment(text="dropped", status="dropped"),
        Commitment(text="late", status="open", deadline=date(2026, 11, 1)), Commitment(text="future", status="open", deadline=date(2026, 12, 1)),
    ])
    db.commit()


def test_analytics_numbers(make_container):
    db = make_container().session_factory()
    seed(db)
    a = analytics(db, TODAY, TZ, weeks=3)
    assert [w["week_start"] for w in a["weeks"]] == ["2026-10-26", "2026-11-02", "2026-11-09"]
    w_prev, w_now = a["weeks"][1], a["weeks"][2]
    assert (w_prev["created"], w_prev["postponed"], w_prev["completed"]) == (1, 2, 0)
    assert (w_now["created"], w_now["completed"], w_now["planned_items"], w_now["planned_done"], w_now["minutes_worked"]) == (1, 1, 2, 1, 120)
    assert a["commitments"] == {"done": 1, "dropped": 1, "overdue": 1, "kept_rate": 0.33}  # the future one is neither kept nor missed yet
    assert a["estimates"] == {"tasks_measured": 1, "median_actual_vs_estimate": 2.0}  # 120 min worked vs 60 estimated
    assert a["postponement"]["total"] == 2 and a["postponement"]["avg_before_completion"] == 2.0
    assert a["postponement"]["top_reasons"] == [{"reason": "too big", "count": 2}]  # case-insensitive grouping
    assert a["completions_by_weekday"]["Tue"] == 1 and sum(a["completions_by_weekday"].values()) == 1


def test_streak_counts_consecutive_active_days_and_tolerates_an_empty_today(make_container):
    db = make_container().session_factory()
    for d in (date(2026, 11, 7), date(2026, 11, 8), date(2026, 11, 9), date(2026, 11, 10)):  # today (11th) has nothing yet
        db.add(WorkSession(started_at=at(d), minutes=10))
    db.add(WorkSession(started_at=at(date(2026, 11, 3)), minutes=10))  # gap, then an older single day
    db.commit()
    s = analytics(db, TODAY, TZ)["streak"]
    assert s["current_days"] == 4 and s["longest_days"] == 4
    db.add(WorkSession(started_at=at(date(2026, 11, 6)), minutes=10))
    db.commit()
    assert analytics(db, TODAY, TZ)["streak"]["current_days"] == 5
    db2 = analytics(db, date(2026, 11, 20), TZ)["streak"]
    assert db2["current_days"] == 0  # two empty days in a row break it


def test_empty_database_gives_zeros_and_nulls_not_errors(make_container):
    db = make_container().session_factory()
    a = analytics(db, TODAY, TZ, weeks=2)
    assert len(a["weeks"]) == 2 and all(w["completed"] == 0 for w in a["weeks"])
    assert a["commitments"]["kept_rate"] is None and a["estimates"]["median_actual_vs_estimate"] is None
    assert a["postponement"]["avg_before_completion"] is None and a["streak"]["current_days"] == 0


def test_timezone_boundaries_use_the_users_day(make_container):
    db = make_container().session_factory()
    karachi = ZoneInfo("Asia/Karachi")  # UTC+5: 21:00 UTC on the 9th is already the 10th locally
    db.add(WorkSession(started_at=datetime(2026, 11, 9, 21, 0), minutes=30))
    db.commit()
    weeks = analytics(db, date(2026, 11, 10), karachi, weeks=2)["weeks"]
    assert weeks[-1]["minutes_worked"] == 30
    assert analytics(db, date(2026, 11, 10), karachi)["streak"]["current_days"] == 1


def test_analytics_endpoint():
    client = TestClient(create_app(make_settings(), ScriptedLLM([])))
    body = client.get("/api/analytics?weeks=4").json()
    assert len(body["weeks"]) == 4 and set(body) >= {"streak", "commitments", "estimates", "postponement", "completions_by_weekday"}
    assert client.get("/api/analytics?weeks=1").status_code == 422
