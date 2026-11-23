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
