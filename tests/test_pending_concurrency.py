"""Approvals that arrive at the same time, or in the wrong order, must never run an action twice or after a reject.

The deterministic tests force the exact interleaving that used to be unsafe: both requests read the pending action
while it is still 'pending', then one of them finishes first. The threaded test does the same thing for real, with
separate connections to a file database.
"""
from __future__ import annotations

import threading
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.config import Settings
from app.container import build_container
from app.db.models import AuditLog, PendingAction, Task
from app.errors import ConflictError
from app.tools.base import ToolContext
from tests.fakes import ScriptedLLM


def _queue_tainted_create(c, db) -> int:
    """A WRITE in a tainted session is queued. Running it twice would create two tasks, so it shows double execution."""
    ctx = ToolContext(db=db, tz=ZoneInfo("UTC"), tainted=True)
    out = c.executor.run(ctx, "create_task", {"title": "From a web page"})
    assert out.status == "pending_confirmation"
    return out.pending_action_id


def _interleave(c, monkeypatch, first_ctx, then):
    """When `first_ctx` has read the pending action (still 'pending'), run `then()` to completion before it continues."""
    original = c.executor._get_pending
    fired = []

    def racing(ctx, pending_id):
        pa = original(ctx, pending_id)
        if ctx is first_ctx and not fired:
            fired.append(True)
            then()
        return pa

    monkeypatch.setattr(c.executor, "_get_pending", racing)


@pytest.fixture
def two_sessions(make_container):
    c = make_container()
    a, b = c.session_factory(), c.session_factory()
    yield c, a, b
    a.close()
    b.close()


def _titles(db) -> list[str]:
    return [t.title for t in db.scalars(select(Task)).all()]


def test_two_approvals_at_once_run_the_action_exactly_once(two_sessions, monkeypatch):
    c, a, b = two_sessions
    pid = _queue_tainted_create(c, a)
    ctx_a, ctx_b = ToolContext(db=a, tz=ZoneInfo("UTC")), ToolContext(db=b, tz=ZoneInfo("UTC"))
    second = {}
    _interleave(c, monkeypatch, ctx_a, lambda: second.setdefault("outcome", c.executor.confirm(ctx_b, pid)))

    with pytest.raises(ConflictError, match="already confirmed"):
        c.executor.confirm(ctx_a, pid)

    assert second["outcome"].status == "ok"
    assert _titles(b) == ["From a web page"]  # once, not twice
    ok_rows = b.scalars(select(AuditLog).where(AuditLog.pending_action_id == pid, AuditLog.status == "ok")).all()
    assert len(ok_rows) == 1


def test_a_reject_that_lands_first_wins_over_a_slower_approval(two_sessions, monkeypatch):
    c, a, b = two_sessions
    pid = _queue_tainted_create(c, a)
    ctx_a, ctx_b = ToolContext(db=a, tz=ZoneInfo("UTC")), ToolContext(db=b, tz=ZoneInfo("UTC"))
    _interleave(c, monkeypatch, ctx_a, lambda: c.executor.reject(ctx_b, pid))

    with pytest.raises(ConflictError, match="already rejected"):
        c.executor.confirm(ctx_a, pid)

    assert _titles(b) == []
    assert b.get(PendingAction, pid, populate_existing=True).status == "rejected"


def test_a_late_reject_cannot_rewrite_an_action_that_already_ran(two_sessions, monkeypatch):
    c, a, b = two_sessions
    pid = _queue_tainted_create(c, a)
    ctx_a, ctx_b = ToolContext(db=a, tz=ZoneInfo("UTC")), ToolContext(db=b, tz=ZoneInfo("UTC"))
    _interleave(c, monkeypatch, ctx_a, lambda: c.executor.confirm(ctx_b, pid))

    with pytest.raises(ConflictError, match="already confirmed"):
        c.executor.reject(ctx_a, pid)

    # The record must keep saying what really happened: it ran.
    assert b.get(PendingAction, pid, populate_existing=True).status == "confirmed"
    assert _titles(b) == ["From a web page"]


def test_many_threads_approving_the_same_action_run_it_once(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'race.db'}", anthropic_api_key=None, _env_file=None)
    c = build_container(settings, llm=ScriptedLLM([]))
    with c.session_factory() as db:
        pid = _queue_tainted_create(c, db)

    start = threading.Barrier(8)
    results: list[str] = []
    lock = threading.Lock()

    def approve():
        with c.session_factory() as db:
            ctx = ToolContext(db=db, tz=ZoneInfo("UTC"))
            start.wait()
            try:
                status = c.executor.confirm(ctx, pid).status
            except ConflictError:
                status = "conflict"
            with lock:
                results.append(status)

    threads = [threading.Thread(target=approve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == ["conflict"] * 7 + ["ok"]
    with c.session_factory() as db:
        assert _titles(db) == ["From a web page"]
