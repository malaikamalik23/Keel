"""Workflows: triggers, multi-step runs, approval checkpoints, history, retry and recovery."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db.models import Notification, Opportunity, Project, ProjectEntry, Task, Workflow, WorkflowRun, utcnow
from app.services.timeutil import local_to_naive_utc
from app.services.workflows import render
from app.tools.base import ToolContext
from tests.fakes import ScriptedLLM, local_client

TZ = ZoneInfo("Asia/Karachi")
SAT_10 = datetime(2026, 11, 21, 10, 0, tzinfo=TZ)  # a Saturday morning


def wf(db, name, trigger, steps, created=None):
    w = Workflow(name=name, trigger=trigger, steps=steps, checked_at=created or local_to_naive_utc(SAT_10 - timedelta(hours=1), TZ),
                 created_at=created or local_to_naive_utc(SAT_10 - timedelta(hours=1), TZ))
    db.add(w)
    db.commit()
    return w


def runs(db, w):
    return db.query(WorkflowRun).filter_by(workflow_id=w.id).order_by(WorkflowRun.id).all()


def sent(db):
    return [n.text for n in db.query(Notification).filter_by(kind="workflow").order_by(Notification.id)]


@pytest.fixture
def c(make_container):
    return make_container(agent_timezone="Asia/Karachi")


# ------------------------------------------------------------------ creating
def test_creating_a_workflow_from_chat_always_waits_for_the_user(c):
    with c.session_factory() as db:
        out = c.executor.run(ToolContext(db=db, tz=TZ), "create_workflow", {
            "name": "Interview prep",
            "trigger": {"type": "event", "event": "opportunity_status", "status": "interview"},
            "steps": [{"tool": "create_task", "args": {"title": "Prepare for {{event.title}}", "due_date": "{{today+2}}"}},
                      {"tool": "delete_task", "args": {"task_id": 1}}],
        })
        assert out.status == "pending_confirmation"
        summary = out.result["summary"]
        assert "When an application moves to 'interview'" in summary and "2. delete_task" in summary and "[waits for your approval]" in summary
        assert db.query(Workflow).count() == 0


@pytest.mark.parametrize("steps,why", [
    ([{"tool": "web_fetch", "args": {"url": "https://example.com"}}], "reads external content"),
    ([{"tool": "read_note", "args": {"path": "x.md"}}], "is not a tool"),  # notes aren't even configured here
    ([{"tool": "create_workflow", "args": {}}], "is not a tool workflows can use"),
    ([{"tool": "create_task", "args": {"title": "x", "colour": "red"}}], "no argument(s) colour"),
    ([{"tool": "delete_task", "args": {}}], "delete_task needs task_id"),
    ([{"tool": "create_task", "notify": "both"}], "either a tool call or a notify"),
    ([], "at least 1 item"),
])
def test_bad_definitions_are_refused(c, steps, why):
    with c.session_factory() as db:
        out = c.executor.run_as_user(ToolContext(db=db, tz=TZ), "create_workflow", {"name": "x", "trigger": {"type": "manual"}, "steps": steps})
        assert out.status == "error" and why in out.result["error"], out.result


def test_templates_fill_in_values_without_an_expression_language():
    scope = {"today": SAT_10.date(), "event": {"title": "Google STEP", "id": 7}, "steps": [{"task": {"id": 42}}]}
    assert render({"title": "Prep {{event.title}}", "due_date": "{{today+3}}", "task_id": "{{steps.1.task.id}}"}, scope) == {
        "title": "Prep Google STEP", "due_date": "2026-11-24", "task_id": 42}
    with pytest.raises(ValueError):
        render("{{event.__class__}}", scope)
    with pytest.raises(ValueError):
        render("{{steps.2.task}}", scope)


# ------------------------------------------------------------------ triggers and runs
def test_event_trigger_runs_every_step_once_per_event(c):
    with c.session_factory() as db:
        db.add(Opportunity(title="Old one", documents=[], created_at=local_to_naive_utc(SAT_10 - timedelta(days=1), TZ)))  # before the workflow
        db.commit()
        w = wf(db, "New application", {"type": "event", "event": "opportunity_added"}, [
            {"tool": "create_task", "args": {"title": "Gather documents for {{event.title}}", "due_date": "{{today+3}}", "priority": "high"}},
            {"notify": "Added task #{{steps.1.task.id}} for {{event.title}}."},
        ])
        db.add(Opportunity(title="Google STEP", documents=[], created_at=local_to_naive_utc(SAT_10 - timedelta(minutes=5), TZ)))
        db.commit()
        report = c.workflows.tick(db, SAT_10)
        assert report == {"started": 1, "steps": 2}
        task = db.query(Task).one()
        assert task.title == "Gather documents for Google STEP" and str(task.due_date) == "2026-11-24" and task.priority == "high"
        assert sent(db) == [f"Added task #{task.id} for Google STEP."]
        [run] = runs(db, w)
        assert run.status == "done" and [e["state"] for e in run.log] == ["started", "ok", "started", "ok"]
        assert c.workflows.tick(db, SAT_10 + timedelta(minutes=1)) == {"started": 0, "steps": 0}  # never twice
        audit = db.execute(__import__("sqlalchemy").text("select actor from audit_log where tool_name='create_task'")).scalars().all()
        assert audit == ["workflow"]


def test_status_and_title_filters(c):
    with c.session_factory() as db:
        w = wf(db, "Interview", {"type": "event", "event": "opportunity_status", "status": "interview", "title_contains": "google"}, [
            {"notify": "Interview stage: {{event.title}}"}])
        o1 = Opportunity(title="Google STEP", documents=[], status="interview")
        o2 = Opportunity(title="Acme intern", documents=[], status="interview")
        o3 = Opportunity(title="Google AI", documents=[], status="applied")
        db.add_all([o1, o2, o3])
        db.commit()
        c.workflows.tick(db, SAT_10)
        assert sent(db) == ["Interview stage: Google STEP"]
        o1.notes = "edited later"  # an unrelated edit must not start it again
        db.commit()
        c.workflows.tick(db, SAT_10 + timedelta(minutes=2))
        assert len(runs(db, w)) == 1


def test_blocker_and_task_completed_events(c):
    with c.session_factory() as db:
        p = Project(name="FYP")
        db.add(p)
        db.commit()
        wf(db, "Unblock", {"type": "event", "event": "blocker_logged"}, [
            {"tool": "create_task", "args": {"title": "Unblock {{event.project}}: {{event.text}}", "project_id": "{{event.project_id}}"}}])
        done = wf(db, "Done", {"type": "event", "event": "task_completed", "title_contains": "chapter"}, [{"notify": "Finished {{event.title}}"}])
        db.add(ProjectEntry(project_id=p.id, kind="blocker", text="No GPU"))
        db.add(Task(title="Write chapter 2", status="done", completed_at=utcnow()))
        db.add(Task(title="Laundry", status="done", completed_at=utcnow()))
        db.commit()
        c.workflows.tick(db, SAT_10)
        assert db.query(Task).filter_by(title="Unblock FYP: No GPU").one().project_id == p.id
        assert [r.event["title"] for r in runs(db, done)] == ["Write chapter 2"]


def test_schedule_runs_on_its_days_once_and_not_the_minute_it_is_made(c):
    sun_8pm = datetime(2026, 11, 22, 20, 5, tzinfo=TZ)
    with c.session_factory() as db:
        w = wf(db, "Weekly review", {"type": "schedule", "time": "20:00", "days": ["sun"]}, [
            {"tool": "accountability_report", "args": {"window_days": 7}},
            {"notify": "Your week: {{steps.1.tasks.completed}} done, {{steps.1.tasks.overdue}} overdue."}])
        late = wf(db, "Made late", {"type": "schedule", "time": "09:00"}, [{"notify": "morning"}],
                  created=local_to_naive_utc(SAT_10, TZ))  # made at 10:00, after today's 09:00
        c.workflows.tick(db, SAT_10.replace(hour=20, minute=5))  # Saturday: not a Sunday
        assert runs(db, w) == [] and runs(db, late) == []
        c.workflows.tick(db, sun_8pm)
        c.workflows.tick(db, sun_8pm + timedelta(minutes=30))
        assert len(runs(db, w)) == 1 and sent(db) == ["Your week: 0 done, 0 overdue."]
        c.workflows.tick(db, sun_8pm.replace(hour=23, minute=30))  # past the 3-hour grace: a missed day stays missed
        c.workflows.tick(db, datetime(2026, 11, 23, 9, 10, tzinfo=TZ))
        assert len(runs(db, late)) == 1  # first run the next day


# ------------------------------------------------------------------ approvals
def test_high_impact_steps_wait_for_the_users_approval(c):
    with c.session_factory() as db:
        db.add(Task(title="Old draft"))
        db.commit()
        w = wf(db, "Cleanup", {"type": "manual"}, [{"notify": "Cleaning up"}, {"tool": "delete_task", "args": {"task_id": 1}, "approve": True}])
        run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
        c.workflows.advance(db, run, SAT_10)
        assert run.status == "waiting_approval" and run.step_index == 1 and db.query(Task).count() == 1
        assert any("step 2 (delete_task) needs your approval" in t for t in sent(db))
        c.workflows.tick(db, SAT_10 + timedelta(minutes=5))  # ticking doesn't move it
        assert run.status == "waiting_approval"
        c.workflows.approve(db, run, SAT_10 + timedelta(minutes=6))
        assert run.status == "done" and db.query(Task).count() == 0


def test_a_high_impact_step_never_runs_unapproved_even_if_the_row_was_edited(c):
    with c.session_factory() as db:
        db.add(Task(title="Keep me"))
        db.commit()
        w = wf(db, "Sneaky", {"type": "manual"}, [{"tool": "delete_task", "args": {"task_id": 1}}])  # "approve" missing
        run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
        c.workflows.advance(db, run, SAT_10)
        assert db.query(Task).count() == 1 and "needs your approval" in run.error
        w2 = wf(db, "Reader", {"type": "manual"}, [{"tool": "web_fetch", "args": {"url": "https://example.com"}}])
        run2 = c.workflows.start(db, w2, "manual:2", {"type": "manual"})
        c.workflows.advance(db, run2, SAT_10)
        assert "external content" in run2.error


def test_the_model_has_no_way_to_approve_a_run(c):
    names = {t.name for t in c.registry.all()}
    assert not any("approve" in n for n in names)
    assert c.registry.get("create_workflow").permission.value == "high_impact"


# ------------------------------------------------------------------ retry and recovery
def test_failed_steps_retry_with_backoff_then_fail_and_can_be_retried(c):
    with c.session_factory() as db:
        w = wf(db, "Finish", {"type": "manual"}, [{"tool": "complete_task", "args": {"task_id": 5}}, {"notify": "done"}])
        run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
        c.workflows.advance(db, run, SAT_10)
        assert run.status == "retrying" and run.attempts == 1 and "No task" in run.error
        c.workflows.tick(db, SAT_10 + timedelta(seconds=30))  # backoff not over: nothing happens
        assert run.attempts == 1
        c.workflows.tick(db, SAT_10 + timedelta(minutes=1))
        assert run.attempts == 2
        c.workflows.tick(db, SAT_10 + timedelta(minutes=7))
        assert run.status == "failed" and run.attempts == 3
        assert any("stopped at step 1 after 3 tries" in t for t in sent(db))
        db.add(Task(id=5, title="Now it exists"))
        db.commit()
        c.workflows.retry(db, run, SAT_10 + timedelta(hours=1))
        assert run.status == "done" and db.get(Task, 5).status == "done"
        assert [e["state"] for e in run.log].count("error") == 3


def test_recovery_after_the_app_stopped_mid_step(c):
    with c.session_factory() as db:
        w = wf(db, "Two writes", {"type": "manual"}, [{"tool": "create_task", "args": {"title": "A"}}])
        run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
        run.log = [{"step": 0, "state": "started", "at": "x"}]  # crashed after starting the write
        db.commit()
        c.workflows.tick(db, SAT_10)
        assert run.status == "waiting_approval" and db.query(Task).count() == 0  # not done twice blindly
        assert any("was interrupted" in t for t in sent(db))
        c.workflows.approve(db, run, SAT_10)
        assert run.status == "done" and db.query(Task).count() == 1

        reader = wf(db, "Read", {"type": "manual"}, [{"tool": "list_tasks", "args": {}}])
        r2 = c.workflows.start(db, reader, "manual:2", {"type": "manual"})
        r2.log = [{"step": 0, "state": "started", "at": "x"}]
        db.commit()
        c.workflows.tick(db, SAT_10)
        assert r2.status == "done"  # a read is simply run again


def test_messages_in_quiet_hours_wait_instead_of_failing(c):
    late = datetime(2026, 11, 21, 23, 30, tzinfo=TZ)
    with c.session_factory() as db:
        w = wf(db, "Late", {"type": "manual"}, [{"notify": "hello"}])
        run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
        c.workflows.advance(db, run, late)
        assert run.status == "running" and run.attempts == 0 and sent(db) == []
        c.workflows.tick(db, datetime(2026, 11, 22, 7, 30, tzinfo=TZ))
        assert run.status == "done" and sent(db) == ["hello"]


# ------------------------------------------------------------------ chat and API
def test_run_workflow_from_chat_runs_now(c):
    with c.session_factory() as db:
        w = wf(db, "Ping", {"type": "manual"}, [{"tool": "create_task", "args": {"title": "Made by {{event.type}} run"}}])
        out = c.executor.run(ToolContext(db=db, tz=TZ), "run_workflow", {"workflow_id": w.id})
        assert out.status == "ok" and out.result["run"]["status"] == "done"
        assert db.query(Task).one().title == "Made by manual run"
        hist = c.executor.run(ToolContext(db=db, tz=TZ), "workflow_history", {}).result["runs"]
        assert hist[0]["status"] == "done" and hist[0]["log"][-1]["state"] == "ok"


def test_workflows_api(make_container):
    from app.api.main import create_app
    from tests.conftest import make_settings

    client = local_client(create_app(make_settings(agent_timezone="Asia/Karachi"), ScriptedLLM([])))
    templates = client.get("/api/workflow-templates").json()
    assert {t["key"] for t in templates} >= {"application-docs", "interview-prep", "weekly-review", "blocker-task"}
    t = next(t for t in templates if t["key"] == "weekly-review")
    made = client.post("/api/workflows", json={"name": t["name"], "trigger": t["trigger"], "steps": t["steps"]})
    assert made.status_code == 201, made.text
    wid = made.json()["workflow"]["id"]
    assert client.patch(f"/api/workflows/{wid}", json={"enabled": False}).json()["workflow"]["enabled"] is False
    ran = client.post(f"/api/workflows/{wid}/run")
    assert ran.status_code == 200 and ran.json()["run"]["status"] in ("done", "running")
    listed = client.get("/api/workflows").json()
    assert listed[0]["runs"][0]["log"] and "every sun at 20:00" in listed[0]["description"]
    rid = listed[0]["runs"][0]["id"]
    assert client.post(f"/api/workflow-runs/{rid}/approve").status_code == 409  # not waiting for approval
    assert client.post(f"/api/workflow-runs/{rid}/explode").status_code == 422
    assert client.delete(f"/api/workflows/{wid}").status_code == 204
    assert client.get("/api/workflows").json() == []
    bad = client.post("/api/workflows", json={"name": "x", "trigger": {"type": "manual"}, "steps": [{"tool": "web_fetch", "args": {"url": "https://example.com"}}]})
    assert bad.status_code == 422


def test_from_chat_to_approval_to_a_run(c):
    """The model proposes a workflow, the user approves it, and the next tick runs it on a real event."""
    from tests.fakes import text, tool_call

    llm = ScriptedLLM([
        tool_call("create_workflow", {"name": "New scholarship", "trigger": {"type": "event", "event": "opportunity_added"},
                                      "steps": [{"tool": "create_task", "args": {"title": "Gather documents for {{event.title}}"}}]}),
        text("I've set that up; approve it in Approvals."),
    ])
    from app.agent.loop import Agent
    from tests.conftest import make_settings

    agent = Agent(llm, c.executor, make_settings(agent_timezone="Asia/Karachi"))
    with c.session_factory() as db:
        result = agent.chat(db, "Whenever I add a new scholarship, create a task to gather its documents")
        assert result.tool_calls[0].status == "pending_confirmation" and db.query(Workflow).count() == 0
        pending_id = result.tool_calls[0].result["pending_action_id"]
        assert c.executor.confirm(ToolContext(db=db, tz=TZ), pending_id).status == "ok"  # the user approves
        w = db.query(Workflow).one()
        w.checked_at = local_to_naive_utc(SAT_10 - timedelta(minutes=30), TZ)
        db.add(Opportunity(title="Fulbright", kind="scholarship", documents=[], created_at=local_to_naive_utc(SAT_10 - timedelta(minutes=1), TZ)))
        db.commit()
        c.workflows.tick(db, SAT_10)
        assert db.query(Task).one().title == "Gather documents for Fulbright"
