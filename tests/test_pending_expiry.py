from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, PendingAction, Task, utcnow
from app.errors import ConflictError
from app.tools.base import ToolContext


@pytest.fixture
def env(make_container):
    c = make_container(pending_action_ttl_hours=1)
    db = c.session_factory()
    ctx = ToolContext(db=db, tz=ZoneInfo("UTC"))
    yield c, ctx, db
    db.close()


def _age(db, pending_id: int, hours: float) -> None:
    pa = db.get(PendingAction, pending_id)
    pa.created_at = utcnow() - timedelta(hours=hours)
    db.commit()


def test_fresh_pending_action_is_not_expired(env):
    c, ctx, db = env
    c.executor.run(ctx, "create_task", {"title": "temp"})
    out = c.executor.run(ctx, "delete_task", {"task_id": 1})
    pid = out.pending_action_id
    outcome = c.executor.confirm(ctx, pid)
    assert outcome.status == "ok"


def test_stale_pending_action_expires_on_confirm_attempt(env):
    c, ctx, db = env
    c.executor.run(ctx, "create_task", {"title": "temp"})
    out = c.executor.run(ctx, "delete_task", {"task_id": 1})
    pid = out.pending_action_id
    _age(db, pid, hours=2)  # older than the 1-hour TTL

    with pytest.raises(ConflictError, match="expired"):
        c.executor.confirm(ctx, pid)

    pa = db.get(PendingAction, pid)
    assert pa.status == "expired"
    assert pa.resolved_at is not None
    # the task must still exist -- an expired action must never have run
    assert db.get(Task, 1) is not None
    statuses = [a.status for a in db.scalars(select(AuditLog))]
    assert "expired" in statuses


def test_stale_pending_action_also_blocks_reject(env):
    c, ctx, db = env
    c.executor.run(ctx, "create_task", {"title": "temp"})
    out = c.executor.run(ctx, "delete_task", {"task_id": 1})
    pid = out.pending_action_id
    _age(db, pid, hours=2)

    with pytest.raises(ConflictError):
        c.executor.reject(ctx, pid)
    assert db.get(PendingAction, pid).status == "expired"


def test_expire_stale_sweeps_multiple_actions(env):
    c, ctx, db = env
    c.executor.run(ctx, "create_task", {"title": "a"})
    c.executor.run(ctx, "create_task", {"title": "b"})
    p1 = c.executor.run(ctx, "delete_task", {"task_id": 1}).pending_action_id
    p2 = c.executor.run(ctx, "delete_task", {"task_id": 2}).pending_action_id
    _age(db, p1, hours=5)
    _age(db, p2, hours=5)

    count = c.executor.expire_stale(db)
    assert count == 2
    assert {db.get(PendingAction, p1).status, db.get(PendingAction, p2).status} == {"expired"}


def test_ttl_none_disables_expiry(make_container):
    c = make_container(pending_action_ttl_hours=None)
    db = c.session_factory()
    ctx = ToolContext(db=db, tz=ZoneInfo("UTC"))
    c.executor.run(ctx, "create_task", {"title": "temp"})
    pid = c.executor.run(ctx, "delete_task", {"task_id": 1}).pending_action_id
    _age(db, pid, hours=24 * 365)
    outcome = c.executor.confirm(ctx, pid)
    assert outcome.status == "ok"
    db.close()


def test_agent_chat_sweeps_expired_actions_each_turn(make_container):
    from tests.fakes import ScriptedLLM, text, tool_call

    llm = ScriptedLLM([
        tool_call("create_task", {"title": "temp"}, id="a"), text("ok"),
        tool_call("delete_task", {"task_id": 1}, id="b"), text("pending"),
        text("just chatting"),
    ])
    c = make_container(llm, pending_action_ttl_hours=1)
    with c.session_factory() as db:
        first = c.agent.chat(db, "add temp")
        second = c.agent.chat(db, "delete it", first.conversation_id)
        pid = second.pending_action_ids[0]
        _age(db, pid, hours=2)

        c.agent.chat(db, "unrelated message", first.conversation_id)
        assert db.get(PendingAction, pid).status == "expired"
