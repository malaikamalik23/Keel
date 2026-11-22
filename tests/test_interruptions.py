"""Concurrency and interruptions: two approvals of the same workflow step, a restart while approvals and workflow steps
are waiting, and two schedulers ticking the same database at the same moment."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.container import build_container
from app.db.models import Notification, PendingAction, Reminder, Task, Workflow, WorkflowRun
from app.tools.base import ToolContext
from tests.fakes import ScriptedLLM

TZ = ZoneInfo("Asia/Karachi")
NOW = datetime(2026, 11, 23, 10, 0, tzinfo=TZ)


def _settings(path, **kw):
    return Settings(database_url=f"sqlite:///{path.as_posix()}", anthropic_api_key=None, _env_file=None,
                    agent_timezone="Asia/Karachi", **kw)


def _waiting_delete(c, db) -> WorkflowRun:
    """A run paused at a high-impact step (delete a task): running it twice would show as an error or a double delete."""
    db.add_all([Task(title="Old draft"), Task(title="Keep me")])
    w = Workflow(name="Cleanup", trigger={"type": "manual"},
                 steps=[{"tool": "delete_task", "args": {"task_id": 1}, "approve": True}, {"tool": "create_task", "args": {"title": "after"}}])
    db.add(w)
    db.commit()
    run = c.workflows.start(db, w, "manual:1", {"type": "manual"})
    c.workflows.advance(db, run, NOW)
    assert run.status == "waiting_approval"
    return run


def test_two_approvals_of_the_same_workflow_step_run_it_once(tmp_path):
    c = build_container(_settings(tmp_path / "wf.db"), llm=ScriptedLLM([]))
    with c.session_factory() as db:
        run_id = _waiting_delete(c, db).id
    a, b = c.session_factory(), c.session_factory()
    try:
        ra, rb = a.get(WorkflowRun, run_id), b.get(WorkflowRun, run_id)  # both see "waiting_approval"
        c.workflows.approve(a, ra, NOW)
        with pytest.raises(ValueError):
            c.workflows.approve(b, rb, NOW)  # a stale second click (another tab) is refused
    finally:
        a.close()
        b.close()
    with c.session_factory() as db:
        assert [t.title for t in db.query(Task).order_by(Task.id)] == ["Keep me", "after"]  # "after" created once
        assert db.get(WorkflowRun, run_id).status == "done"


def test_many_threads_approving_one_workflow_step(tmp_path):
    c = build_container(_settings(tmp_path / "wf2.db"), llm=ScriptedLLM([]))
    with c.session_factory() as db:
        run_id = _waiting_delete(c, db).id
    start, results, lock = threading.Barrier(6), [], threading.Lock()

    def approve():
        with c.session_factory() as db:
            run = db.get(WorkflowRun, run_id)
            start.wait()
            try:
                c.workflows.approve(db, run, NOW)
                r = "ok"
            except ValueError:
                r = "refused"
            except Exception as exc:  # noqa: BLE001 - a locked database must surface as a refusal, never a double run
                r = type(exc).__name__
            with lock:
                results.append(r)

    threads = [threading.Thread(target=approve) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results.count("ok") == 1, results
    with c.session_factory() as db:
        assert db.query(Task).filter_by(title="after").count() == 1


def test_a_cancel_after_the_step_already_ran_is_refused(tmp_path):
    c = build_container(_settings(tmp_path / "wf3.db"), llm=ScriptedLLM([]))
    with c.session_factory() as db:
        run_id = _waiting_delete(c, db).id
    a, b = c.session_factory(), c.session_factory()
    try:
        ra, rb = a.get(WorkflowRun, run_id), b.get(WorkflowRun, run_id)
        c.workflows.approve(a, ra, NOW)
        with pytest.raises(ValueError):
            c.workflows.cancel(b, rb)  # stale: the run is already done
    finally:
        a.close()
        b.close()
    with c.session_factory() as db:
        assert db.get(WorkflowRun, run_id).status == "done"


def test_restarting_keel_keeps_waiting_approvals_and_workflow_steps_answerable(tmp_path):
    path = tmp_path / "restart.db"
    first = build_container(_settings(path), llm=ScriptedLLM([]))
    with first.session_factory() as db:
        ctx = ToolContext(db=db, tz=TZ, tainted=True)
        queued = first.executor.run(ctx, "create_task", {"title": "From a web page"})
        db.commit()
        run_id = _waiting_delete(first, db).id
    first.engine.dispose()  # Keel stops (a crash or a restart)

    second = build_container(_settings(path), llm=ScriptedLLM([]))
    with second.session_factory() as db:
        assert db.get(PendingAction, queued.pending_action_id).status == "pending"
        assert second.executor.confirm(ToolContext(db=db, tz=TZ), queued.pending_action_id).status == "ok"
        db.commit()
        second.workflows.approve(db, db.get(WorkflowRun, run_id), NOW)
        assert db.get(WorkflowRun, run_id).status == "done"
        assert sorted(t.title for t in db.query(Task)) == ["From a web page", "Keep me", "after"]


def test_two_schedulers_on_one_database_send_a_reminder_once(tmp_path):
    """The docs say to run one scheduler; a second one by mistake (two terminals) must still not double-send."""
    from tests.test_phase4 import FailingNotifier

    path = tmp_path / "two.db"
    notifiers = [FailingNotifier(fail=False), FailingNotifier(fail=False)]
    cs = [build_container(_settings(path), llm=ScriptedLLM([]), notifier=n) for n in notifiers]
    with cs[0].session_factory() as db:
        db.add(Reminder(text="Submit the form", fire_at=datetime(2026, 11, 23, 4, 59)))
        db.commit()
    start = threading.Barrier(2)

    def tick(c):
        start.wait()
        try:
            c.scheduler.tick(NOW + timedelta(minutes=1))
        except Exception:  # noqa: BLE001, S110 - a locked database is fine; sending twice is not
            pass  # nosec B110

    threads = [threading.Thread(target=tick, args=(c,)) for c in cs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    everything = [m for n in notifiers for m in n.sent]
    assert everything.count("Reminder: Submit the form") == 1, everything
    assert len(everything) == len(set(everything))  # nothing (the morning check-in included) went out twice
    assert [len(n.sent) > 0 for n in notifiers].count(True) == 1  # one scheduler did the work, the other stood aside
    with cs[0].session_factory() as db:
        assert db.query(Notification).filter_by(status="sent").count() == len(everything)


def test_the_other_scheduler_takes_over_when_the_first_stops_or_dies(tmp_path):
    from app.db.models import UserSetting

    path = tmp_path / "lease.db"
    a, b = (build_container(_settings(path), llm=ScriptedLLM([])) for _ in range(2))
    assert "skipped" not in a.scheduler.tick(NOW)
    assert b.scheduler.tick(NOW)["skipped"] == "another_scheduler"
    a.scheduler.stop()  # a clean stop hands the lease back at once
    assert "skipped" not in b.scheduler.tick(NOW)
    assert a.scheduler.tick(NOW)["skipped"] == "another_scheduler"
    with b.session_factory() as db:  # b dies without cleaning up: its lease lapses
        row = db.get(UserSetting, "scheduler:lease")
        row.value = {**row.value, "until": "2000-01-01T00:00:00"}
        db.commit()
    assert "skipped" not in a.scheduler.tick(NOW)
