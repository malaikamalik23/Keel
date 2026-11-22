"""Per-integration switches and retention rules (Services tab)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.agent.loop import Agent
from app.db.models import (
    AuditLog,
    Checkin,
    Conversation,
    Message,
    Notification,
    PendingAction,
    Task,
    TaskEvent,
    Workflow,
    WorkflowRun,
)
from app.security.permissions import PermissionPolicy
from app.services import retention, switches
from app.tools import build_default_registry
from app.tools.base import ToolContext
from app.tools.executor import ToolExecutor
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, local_client, text, tool_call
from tests.test_gmail import google  # noqa: F401 - pytest fixture

TZ = ZoneInfo("Asia/Karachi")
NOW = datetime(2026, 11, 23, 12, 0)


def _ctx(db):
    return ToolContext(db=db, tz=TZ)


# ------------------------------------------------------------------ switches
def test_every_integration_tool_has_a_switch():
    """A new Gmail/browser/web/notes tool can't slip past the switches."""
    from app.tools.groups import group_of

    reg = build_default_registry(gmail=object(), browser=object(), google_calendar=object(), github=object(),
                                 fetcher=object(), notes_enabled=True, notes_dir="notes")
    for t in reg.all():
        if group_of(t.name) in ("email", "browser", "github", "notes") or t.name.startswith(("gcal_", "web_")):
            assert switches.integration_of(t.name) in switches.SWITCHES, t.name


def test_a_switched_off_integration_is_hidden_refused_and_its_queued_approvals_die(make_container, google):  # noqa: F811
    fake, client, _ = google
    ex = ToolExecutor(build_default_registry(gmail=client), PermissionPolicy())
    with make_container().session_factory() as db:
        queued = ex.run(_ctx(db), "gmail_send", {"to": "khan@nust.edu.pk", "subject": "Chapter 2", "body": "Attached."})
        db.commit()
        assert queued.status == "pending_confirmation"

        switches.set_switch(db, "gmail", False)
        assert "gmail_send" in switches.hidden_tools(db, [t.name for t in ex.registry.all()])
        assert "gmail_send" not in {s["name"] for s in ex.registry.specs(hidden=switches.hidden_tools(db, [t.name for t in ex.registry.all()]))}
        refused = ex.run(_ctx(db), "gmail_search", {"query": "from:khan"})
        assert refused.status == "error" and "switched off" in refused.result["error"]
        assert ex.run_as_workflow(_ctx(db), "gmail_draft", {"to": "a@b.co", "subject": "x", "body": "y"}, approved=True).status == "error"
        done = ex.confirm(_ctx(db), queued.pending_action_id)  # approved after switching off: not sent
        db.commit()
        assert done.status == "error" and "Nothing was done" in done.result["error"]
        assert db.get(PendingAction, queued.pending_action_id).status == "failed"
        assert db.query(AuditLog).filter_by(tool_name="gmail_send", status="error").count() == 1
        assert fake.sent() == []

        switches.set_switch(db, "gmail", True)
        assert ex.run(_ctx(db), "gmail_search", {"query": "from:khan"}).status == "ok"
    with pytest.raises(ValueError):
        switches.set_switch(db, "telepathy", False)


def test_the_model_is_told_and_never_offered_switched_off_tools(make_container, google):  # noqa: F811
    _, client, _ = google
    ex = ToolExecutor(build_default_registry(gmail=client), PermissionPolicy())
    llm = ScriptedLLM([tool_call("load_tools", {"groups": ["email"]}), text("Gmail is switched off, so I can't check your inbox.")])
    agent = Agent(llm, ex, make_settings())
    with make_container().session_factory() as db:
        switches.set_switch(db, "gmail", False)
        agent.chat(db, "check my email from Dr. Khan")
    offered = {t["name"] for call in llm.calls for t in call["tools"]}
    assert not any(n.startswith("gmail_") for n in offered)
    result = llm.calls[1]["messages"][-1]["content"][0]["content"]
    assert "switched_off" in result and "Gmail" in result


def test_switching_off_messages_stops_reminders():
    from app.db.models import Reminder
    from tests.test_phase4 import make

    c, n = make()
    with c.session_factory() as db:
        db.add(Reminder(text="Call", fire_at=NOW - timedelta(minutes=1)))
        db.commit()
        switches.set_switch(db, "messages", False)
    report = c.scheduler.tick(NOW.replace(tzinfo=ZoneInfo("UTC")))
    assert report["reminders_sent"] == 0 and report["blocked"] == {"switched_off": 1} and n.sent == []
    with c.session_factory() as db:
        switches.set_switch(db, "messages", True)
    assert c.scheduler.tick(NOW.replace(tzinfo=ZoneInfo("UTC")) + timedelta(hours=1))["reminders_sent"] == 1


def test_dashboard_switch_endpoints():
    from app.api.main import create_app

    app = create_app(make_settings(web_fetch_enabled=True), ScriptedLLM([]))
    client = local_client(app)
    rows = {r["id"]: r for r in client.get("/api/services").json()}
    assert rows["web"]["switch"] == {"id": "web", "label": "Reading web pages", "on": True}
    assert "switch" not in rows["gmail"]  # not set up: nothing to switch
    assert client.put("/api/switches/web", json={"on": False}).json() == {"id": "web", "on": False}
    assert {r["id"]: r for r in client.get("/api/services").json()}["web"]["switch"]["on"] is False
    assert client.put("/api/switches/web", json={"on": "no"}).status_code == 422
    assert client.put("/api/switches/nope", json={"on": False}).status_code == 404


# ------------------------------------------------------------------ retention
def _history(db):
    old, new = NOW - timedelta(days=100), NOW - timedelta(days=2)
    task = Task(title="Write FYP intro")
    wf = Workflow(name="w", trigger={"type": "manual"}, steps=[])
    db.add_all([task, wf])
    db.flush()
    for when, tag in ((old, "old"), (new, "new")):
        conv = Conversation(created_at=when)
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=[{"type": "text", "text": tag}], created_at=when))
        db.add(AuditLog(created_at=when, actor="agent", tool_name="list_tasks", permission="read", status="ok"))
        db.add(Notification(kind="nudge", priority="normal", channel="email", text=tag, sent_at=when))
        db.add(Checkin(kind="morning", checkin_date=when.date(), summary=tag, created_at=when))
        db.add(TaskEvent(task_id=task.id, task_title=task.title, type="postponed", at=when))
        db.add(PendingAction(tool_name="delete_task", tool_input={}, summary=tag, status="rejected", created_at=when, resolved_at=when))
        db.add(WorkflowRun(workflow_id=wf.id, dedupe_key=tag, event={}, status="done", finished_at=when, updated_at=when))
    # An old conversation still waiting for an approval is kept, so the approval can still be answered.
    waiting = Conversation(created_at=old)
    db.add(waiting)
    db.flush()
    db.add(Message(conversation_id=waiting.id, role="user", content=[], created_at=old))
    db.add(PendingAction(conversation_id=waiting.id, tool_name="delete_task", tool_input={}, summary="waiting", status="pending", created_at=old))
    db.commit()


def test_nothing_is_deleted_until_you_set_a_rule(make_container):
    with make_container().session_factory() as db:
        _history(db)
        assert retention.apply(db, now=NOW) == {}
        assert db.query(Conversation).count() == 3 and db.query(AuditLog).count() == 2


def test_rules_delete_only_expired_history_and_never_what_you_made(make_container, tmp_path):
    notes = tmp_path / "notes"
    for stamp in ("20260801-090000", "20261121-090000"):
        (notes / ".keel-backups" / stamp).mkdir(parents=True)
        (notes / ".keel-backups" / stamp / "journal.md").write_text("old", encoding="utf-8")
    with make_container().session_factory() as db:
        _history(db)
        for cat in retention.CATEGORIES:
            retention.set_rule(db, cat, 90)
        preview = retention.apply(db, notes_dir=str(notes), now=NOW, dry_run=True)
        assert preview == {c: 1 for c in retention.CATEGORIES}
        assert db.query(AuditLog).count() == 2  # a preview deletes nothing
        assert retention.apply(db, notes_dir=str(notes), now=NOW) == preview
        assert sorted(str(m.content) for m in db.query(Message).all()) == ["[]", "[{'type': 'text', 'text': 'new'}]"]
        assert db.query(Conversation).count() == 2  # the new one and the one with a waiting approval
        assert db.query(PendingAction).filter_by(status="pending").count() == 1
        for model in (AuditLog, Notification, Checkin, TaskEvent, WorkflowRun):
            assert db.query(model).count() == 1, model
        assert db.query(PendingAction).filter_by(status="rejected").count() == 1
        assert db.query(Task).count() == 1 and db.query(Workflow).count() == 1  # what you made stays
        assert sorted(p.name for p in (notes / ".keel-backups").iterdir()) == ["20261121-090000"]
        assert retention.last_run(db) == NOW.isoformat(timespec="seconds")
        assert retention.apply(db, notes_dir=str(notes), now=NOW) == {c: 0 for c in retention.CATEGORIES}


def test_rules_have_floors(make_container):
    with make_container().session_factory() as db:
        with pytest.raises(ValueError, match="at least 90"):
            retention.set_rule(db, "task_history", 30)
        with pytest.raises(ValueError, match="at least 30"):
            retention.set_rule(db, "notifications", 7)
        with pytest.raises(ValueError):
            retention.set_rule(db, "memories", 30)  # what you made isn't a retention category
        retention.set_rule(db, "conversations", 7)
        retention.set_rule(db, "conversations", None)
        assert retention.rules(db)["conversations"] is None


def test_scheduler_runs_rules_once_a_day(make_container):
    c = make_container()
    with c.session_factory() as db:
        _history(db)
        retention.set_rule(db, "audit_log", 30)
    at = NOW.replace(tzinfo=ZoneInfo("UTC"))
    assert c.scheduler.tick(at)["retention_deleted"] == {"audit_log": 1}
    with c.session_factory() as db:
        db.add(AuditLog(created_at=NOW - timedelta(days=60), actor="agent", tool_name="x", permission="read", status="ok"))
        db.commit()
    assert "retention_deleted" not in c.scheduler.tick(at + timedelta(hours=2))  # already ran today
    assert c.scheduler.tick(at + timedelta(days=1))["retention_deleted"] == {"audit_log": 1}


def test_dashboard_retention_endpoints():
    from app.api.main import create_app

    app = create_app(make_settings(), ScriptedLLM([]))
    with app.state.container.session_factory() as db:
        _history(db)
    client = local_client(app)
    cats = {c["id"]: c for c in client.get("/api/retention").json()["categories"]}
    assert cats["audit_log"]["days"] is None and cats["task_history"]["min_days"] == 90
    assert client.put("/api/retention", json={"task_history": 30}).status_code == 422
    assert client.put("/api/retention", json={"memories": 30}).status_code == 422
    assert client.put("/api/retention", json={"audit_log": True}).status_code == 422
    # Preview the rules before saving them: nothing is deleted.
    assert client.post("/api/retention/preview", json={"audit_log": 30}).json()["would_delete"] == {"audit_log": 1}
    assert client.post("/api/retention/apply").json()["deleted"] == {}  # no rule saved yet
    assert client.put("/api/retention", json={"audit_log": 30}).json()["categories"][2]["days"] == 30
    assert client.post("/api/retention/apply").json()["deleted"] == {"audit_log": 1}
