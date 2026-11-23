"""Dashboard task actions: quick add, tick and Undo, reschedule. They run the same tools as chat, audited as the user."""
from __future__ import annotations

from app.api.main import create_app
from app.db.models import AuditLog, TaskEvent
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, local_client


def test_quick_add_tick_undo_and_move():
    app = create_app(make_settings(), ScriptedLLM([]))
    client = local_client(app)
    t = client.post("/api/tasks", json={"title": "Renew passport", "due_date": "2099-01-10", "priority": "high", "ignored": "x"})
    assert t.status_code == 201 and (t.json()["title"], t.json()["due_date"], t.json()["priority"]) == ("Renew passport", "2099-01-10", "high")
    tid = t.json()["id"]
    assert client.post(f"/api/tasks/{tid}/complete").json()["status"] == "done"
    assert client.patch(f"/api/tasks/{tid}", json={"status": "todo"}).json()["status"] == "todo"  # Undo
    moved = client.patch(f"/api/tasks/{tid}", json={"due_date": "2099-01-15"}).json()
    assert moved["due_date"] == "2099-01-15" and moved["postponement_count"] == 1  # later counts as a postponement
    assert client.patch(f"/api/tasks/{tid}", json={"status": "cancelled"}).json()["status"] == "cancelled"  # Undo of an add
    with app.state.container.session_factory() as db:
        assert [e.type for e in db.query(TaskEvent).order_by(TaskEvent.id)] == ["created", "completed", "reopened", "postponed", "cancelled"]
        assert {a.actor for a in db.query(AuditLog)} == {"user"}


def test_bad_task_requests():
    client = local_client(create_app(make_settings(), ScriptedLLM([])))
    assert client.post("/api/tasks", json={}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "due_date": "not-a-date"}).status_code == 422
    assert client.patch("/api/tasks/999", json={"title": "x"}).status_code == 404
    tid = client.post("/api/tasks", json={"title": "A"}).json()["id"]
    assert client.patch(f"/api/tasks/{tid}", json={}).status_code == 422
    assert client.patch(f"/api/tasks/{tid}", json={"status": "exploded"}).status_code == 422


def test_goal_status_and_milestone_ticks():
    from app.db.models import Goal, Milestone

    app = create_app(make_settings(), ScriptedLLM([]))
    client = local_client(app)
    with app.state.container.session_factory() as db:
        g = Goal(title="Learn Rust")
        db.add(g)
        db.flush()
        db.add_all([Milestone(goal_id=g.id, title="Ownership", position=0), Milestone(goal_id=g.id, title="Async", position=1)])
        db.commit()
    assert client.patch("/api/milestones/1", json={"status": "done"}).json()["status"] == "done"
    assert client.patch("/api/milestones/1", json={"status": "finished"}).status_code == 422
    assert client.patch("/api/milestones/99", json={"status": "done"}).status_code == 404
    goal = client.patch("/api/goals/1", json={"status": "achieved"}).json()
    assert goal["status"] == "achieved" and [m["status"] for m in goal["milestones"]] == ["done", "todo"]
    assert client.patch("/api/goals/1", json={}).status_code == 422
    with app.state.container.session_factory() as db:
        assert {(a.tool_name, a.actor) for a in db.query(AuditLog)} >= {("update_milestone", "user"), ("update_goal", "user")}
