from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.db.models import Goal, Task, TaskEvent, WorkSession, utcnow
from app.services import accountability
from app.services.planner import MAX_MAJOR, build_plan, priority_score, rank_tasks, usable_capacity
from app.tools.base import ToolContext
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, text, tool_call

TZ = ZoneInfo("UTC")


@pytest.fixture
def env(make_container):
    c = make_container()
    db = c.session_factory()
    ctx = ToolContext(db=db, tz=TZ)
    yield c, ctx, db
    db.close()


def run(env, tool, **kw):
    c, ctx, _ = env
    out = c.executor.run(ctx, tool, kw)
    assert out.status == "ok", out.result
    return out.result


def events(db, task_id=None):
    q = db.query(TaskEvent)
    if task_id:
        q = q.filter(TaskEvent.task_id == task_id)
    return [(e.type, e.reason) for e in q.order_by(TaskEvent.id)]


# ---------- task_events ----------
def test_task_lifecycle_is_recorded_as_events(env):
    _, ctx, db = env
    tid = run(env, "create_task", title="GRE quant", due_date=(ctx.today() + timedelta(days=1)).isoformat())["task"]["id"]
    run(env, "update_task", task_id=tid, due_date=(ctx.today() + timedelta(days=3)).isoformat(), reason="too big")
    run(env, "complete_task", task_id=tid)
    run(env, "update_task", task_id=tid, status="todo")
    assert events(db, tid) == [("created", None), ("postponed", "too big"), ("completed", None), ("reopened", None)]
    ev = db.query(TaskEvent).filter_by(type="postponed").one()
    assert ev.old_value and ev.new_value and ev.old_value < ev.new_value


def test_pulling_a_deadline_earlier_is_not_a_postponement(env):
    _, ctx, db = env
    tid = run(env, "create_task", title="t", due_date=(ctx.today() + timedelta(days=5)).isoformat())["task"]["id"]
    run(env, "update_task", task_id=tid, due_date=(ctx.today() + timedelta(days=2)).isoformat())
    assert [e[0] for e in events(db, tid)] == ["created"]


def test_events_survive_task_deletion(env):
    c, ctx, db = env
    tid = run(env, "create_task", title="Doomed")["task"]["id"]
    pending = c.executor.run(ctx, "delete_task", {"task_id": tid}).pending_action_id
    assert c.executor.confirm(ctx, pending).status == "ok"
    db.expire_all()
    ev = db.query(TaskEvent).one()
    assert ev.task_id is None and ev.task_title == "Doomed"


def test_milestone_tasks_get_created_events(env):
    _, _, db = env
    gid = run(env, "create_goal", title="Pentesting")["goal"]["id"]
    mid = run(env, "add_milestones", goal_id=gid, titles=["HTTP"])["created"][0]["id"]
    run(env, "milestone_to_tasks", milestone_id=mid, titles=["a", "b"])
    assert [e[0] for e in events(db)] == ["created", "created"]


def test_task_history_tool(env):
    tid = run(env, "create_task", title="x", due_date="2099-01-01")["task"]["id"]
    run(env, "update_task", task_id=tid, due_date="2099-01-05", reason="sick")
    h = run(env, "task_history", task_id=tid)
    assert [e["type"] for e in h["events"]] == ["created", "postponed"] and h["events"][1]["reason"] == "sick"


# ---------- planner ----------
def _t(i, minutes=None, priority="medium", due=None, postponed=0):
    return Task(id=i, title=f"t{i}", estimated_minutes=minutes, priority=priority, due_date=due, postponement_count=postponed)


def test_plan_never_exceeds_usable_capacity():
    tasks = [_t(i, 50) for i in range(1, 11)]
    res = build_plan(tasks, 240)
    assert res.used_minutes <= usable_capacity(240) == 204
    assert sum(c.minutes for c in res.selected) == res.used_minutes
    assert len(res.selected) + len(res.deferred) == 10
    assert all("min" in d["reason"] for d in res.deferred)


def test_plan_caps_major_priorities_at_three_but_fits_small_tasks():
    tasks = [_t(i, 60, "high") for i in range(1, 6)] + [_t(10, 15, "low"), _t(11, 15, "low")]
    res = build_plan(tasks, 600)
    majors = [c for c in res.selected if c.is_major]
    assert len(majors) == MAX_MAJOR
    assert {c.task.id for c in res.selected} >= {10, 11}
    assert sum("major priorities" in d["reason"] for d in res.deferred) == 2


def test_task_longer_than_the_day_is_deferred_with_advice_to_split():
    res = build_plan([_t(1, 600), _t(2, 30)], 240)
    assert [c.task.id for c in res.selected] == [2]
    assert "split" in res.deferred[0]["reason"]


def test_ranking_prefers_overdue_then_priority_and_surfaces_slipping_tasks():
    today = date(2026, 11, 7)
    overdue = _t(1, due=today - timedelta(days=1))
    high_far = _t(2, priority="high", due=today + timedelta(days=30))
    low_none = _t(3, priority="low")
    slipping = _t(4, postponed=4)
    ranked = [t.id for t in rank_tasks([low_none, high_far, slipping, overdue], today)]
    assert ranked[0] == 1 and ranked[-1] == 3
    assert priority_score(slipping, today) > priority_score(_t(5), today)


def test_create_daily_plan_tool_and_replan_keeps_done_items(env):
    _, ctx, db = env
    for i in range(6):
        run(env, "create_task", title=f"task {i}", estimated_minutes=45, due_date=ctx.today().isoformat())
    plan = run(env, "create_daily_plan", capacity_minutes=240)
    assert plan["planned_minutes"] <= plan["usable_minutes"] == 204 and plan["deferred"]
    first = plan["items"][0]
    run(env, "complete_task", task_id=first["task_id"])  # checks off the plan item
    assert run(env, "get_daily_plan")["items"][0]["status"] == "done"

    again = run(env, "create_daily_plan", capacity_minutes=240)
    assert again["items"][0]["task_id"] == first["task_id"] and again["items"][0]["status"] == "done"
    assert len({i["task_id"] for i in again["items"]}) == len(again["items"])  # no duplicates
    assert again["planned_minutes"] <= again["usable_minutes"]


def test_plan_with_explicit_tasks_respects_order_and_validates(env):
    a = run(env, "create_task", title="A", estimated_minutes=30)["task"]["id"]
    b = run(env, "create_task", title="B", estimated_minutes=30)["task"]["id"]
    plan = run(env, "create_daily_plan", task_ids=[b, a], capacity_minutes=240)
    assert [i["task_id"] for i in plan["items"]] == [b, a]
    c, ctx, _ = env
    assert c.executor.run(ctx, "create_daily_plan", {"task_ids": [999]}).status == "error"
    run(env, "complete_task", task_id=a)
    assert c.executor.run(ctx, "create_daily_plan", {"task_ids": [a]}).status == "error"


def test_skipping_a_plan_item_is_recorded_with_reason(env):
    _, _, db = env
    tid = run(env, "create_task", title="avoided", estimated_minutes=30)["task"]["id"]
    item = run(env, "create_daily_plan")["items"][0]["item_id"]
    run(env, "update_plan_item", item_id=item, status="skipped", reason="not in the mood")
    assert ("skipped", "not in the mood") in events(db, tid)


# ---------- work sessions ----------
def test_work_sessions_inherit_project_and_start_the_task(env):
    _, ctx, db = env
    pid = run(env, "create_project", name="FYP")["project"]["id"]
    tid = run(env, "create_task", title="intro")["task"]["id"]
    from app.db.models import Task as T
    db.get(T, tid).project_id = pid
    db.commit()
    s = run(env, "log_work_session", minutes=45, task_id=tid)["session"]
    assert s["project_id"] == pid and db.get(T, tid).status == "in_progress"
    assert run(env, "list_work_sessions")["total_minutes"] == 45
    c, _, _ = env
    assert c.executor.run(ctx, "log_work_session", {"minutes": 5, "task_id": 999}).status == "error"
    assert c.executor.run(ctx, "log_work_session", {"minutes": 0}).status == "error"


# ---------- accountability ----------
def test_accountability_report_reflects_real_history(env):
    _, ctx, db = env
    slipping = run(env, "create_task", title="GRE", due_date=ctx.today().isoformat())["task"]["id"]
    for n, reason in enumerate(["too big", "tired", None], start=1):
        run(env, "update_task", task_id=slipping, due_date=(ctx.today() + timedelta(days=n)).isoformat(), reason=reason)
    done = run(env, "create_task", title="easy")["task"]["id"]
    run(env, "complete_task", task_id=done)
    old = run(env, "create_task", title="old and ignored")["task"]["id"]
    db.get(Task, old).created_at = utcnow() - timedelta(days=20)
    db.commit()
    run(env, "create_daily_plan")

    rep = run(env, "accountability_report", window_days=7)
    assert rep["tasks"]["completed"] == 1 and rep["tasks"]["created"] == 3
    mp = rep["most_postponed"][0]
    assert mp["title"] == "GRE" and mp["postponement_count"] == 3 and mp["reasons_given"] == ["too big", "tired"]
    assert [a["title"] for a in rep["avoided_tasks"]] == ["old and ignored"]
    assert rep["plan_adherence"]["planned_items"] >= 1


def test_neglected_goals_only_flags_inactive_goals(env):
    _, _, db = env
    quiet = run(env, "create_goal", title="Quiet goal")["goal"]["id"]
    busy = run(env, "create_goal", title="Busy goal")["goal"]["id"]
    db.get(Goal, quiet).created_at = utcnow() - timedelta(days=30)
    db.get(Goal, busy).created_at = utcnow() - timedelta(days=30)
    db.commit()
    mid = run(env, "add_milestones", goal_id=busy, titles=["m"])["created"][0]["id"]
    run(env, "milestone_to_tasks", milestone_id=mid, titles=["fresh task"])  # recent activity on `busy`
    flagged = accountability.neglected_goals(db, date.today(), TZ)
    assert [g["title"] for g in flagged] == ["Quiet goal"] and flagged[0]["ever_had_activity"] is False


# ---------- API + context ----------
def test_phase3_http_endpoints_and_context_snapshot():
    llm = ScriptedLLM([
        tool_call("create_task", {"title": "Write intro", "estimated_minutes": 60}, id="a"), text("ok"),
        tool_call("create_daily_plan", {"capacity_minutes": 240}, id="b"), text("Planned."),
        text("seen"),
    ])
    client = TestClient(create_app(make_settings(), llm))
    client.post("/api/chat", json={"message": "add task"})
    client.post("/api/chat", json={"message": "plan my day, 4 hours"})
    plan = client.get("/api/plan").json()
    assert plan["exists"] and plan["items"][0]["title"] == "Write intro"
    assert client.get("/api/tasks/1/events").json()[0]["type"] == "created"
    assert client.get("/api/tasks/99/events").status_code == 404
    rep = client.get("/api/accountability?window_days=3").json()
    assert rep["window"]["days"] == 3 and rep["tasks"]["created"] == 1
    assert client.get("/api/work-sessions").json() == []
    client.post("/api/chat", json={"message": "how am I doing"})
    assert "Today's plan" in llm.calls[-1]["system"] and "Write intro" in llm.calls[-1]["system"]


def test_dashboard_actions_complete_task_and_check_off_plan_items():
    llm = ScriptedLLM([
        tool_call("create_task", {"title": "A", "estimated_minutes": 30}, id="a"), text("ok"),
        tool_call("create_task", {"title": "B", "estimated_minutes": 30}, id="b"), text("ok"),
        tool_call("create_daily_plan", {}, id="c"), text("planned"),
    ])
    client = TestClient(create_app(make_settings(), llm))
    for m in ("a", "b", "plan"):
        client.post("/api/chat", json={"message": m})
    items = client.get("/api/plan").json()["items"]
    done = client.post("/api/tasks/1/complete")
    assert done.status_code == 200 and done.json()["status"] == "done"
    assert client.get("/api/plan").json()["items"][0]["status"] == "done"  # task completion checks off the plan
    r = client.patch(f"/api/plan-items/{items[1]['item_id']}", json={"status": "skipped", "reason": "tired"})
    assert r.json()["status"] == "skipped"
    assert client.patch("/api/plan-items/99", json={"status": "done"}).status_code == 404
    assert client.patch(f"/api/plan-items/{items[1]['item_id']}", json={"status": "bogus"}).status_code == 422
    assert client.post("/api/tasks/99/complete").status_code == 404
    assert [e["type"] for e in client.get("/api/tasks/2/events").json()] == ["created", "skipped"]
