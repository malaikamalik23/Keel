from sqlalchemy import select

from app.agent.loop import STEP_LIMIT_REPLY
from app.db.models import Commitment, Message, PendingAction, Task
from app.llm.base import LLMResponse
from tests.fakes import LoopingLLM, ScriptedLLM, text, tool_call


def test_tool_loop_creates_task_and_persists_full_turn(make_container):
    llm = ScriptedLLM([
        tool_call("create_task", {"title": "GRE quant set", "due_date": "2099-01-01", "priority": "high"}),
        text("Added 'GRE quant set' for Jan 1."),
    ])
    c = make_container(llm)
    with c.session_factory() as db:
        res = c.agent.chat(db, "Add a GRE quant set due Jan 1, high priority")
        assert res.reply.startswith("Added")
        assert [(t.name, t.status) for t in res.tool_calls] == [("create_task", "ok")]
        assert db.scalars(select(Task)).one().priority == "high"
        roles = [m.role for m in db.scalars(select(Message).order_by(Message.id))]
        assert roles == ["user", "assistant", "user", "assistant"]  # user, tool_use, tool_result, final

    # the tool_result was fed back to the model on the second call
    second_call_messages = llm.calls[1]["messages"]
    assert second_call_messages[-1]["content"][0]["type"] == "tool_result"
    assert "current date" in llm.calls[0]["system"].lower()


def test_second_turn_replays_history_in_valid_order(make_container):
    llm = ScriptedLLM([tool_call("list_tasks", {}), text("Nothing yet."), text("Still nothing.")])
    c = make_container(llm)
    with c.session_factory() as db:
        first = c.agent.chat(db, "what's on my list?")
        c.agent.chat(db, "and now?", first.conversation_id)
    msgs = llm.calls[2]["messages"]
    assert msgs[0]["role"] == "user"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]


def test_high_impact_tool_call_is_queued_and_reported_to_the_user(make_container):
    llm = ScriptedLLM([
        tool_call("create_task", {"title": "temp"}, id="a"),
        text("ok"),
        tool_call("delete_task", {"task_id": 1}, id="b"),
        text("Deletion is waiting for your approval."),
    ])
    c = make_container(llm)
    with c.session_factory() as db:
        c.agent.chat(db, "add temp")
        res = c.agent.chat(db, "delete task 1")
        assert res.pending_action_ids and res.tool_calls[0].status == "pending_confirmation"
        assert db.get(Task, 1) is not None
        assert db.scalars(select(PendingAction)).one().status == "pending"


def test_commitment_detection_and_confirmation_round_trip(make_container):
    """Mirrors the spec's example: 'I'll finish this by Friday' -> proposed -> confirmed by the user."""
    llm = ScriptedLLM([
        tool_call("propose_commitment", {"text": "Finish GRE quant set", "deadline": "2099-01-05"}, id="a"),
        text("You said you'd finish the GRE quant set by Friday (Jan 5) — is that right?"),
        tool_call("confirm_commitment", {"commitment_id": 1}, id="b"),
        text("Got it, that's confirmed."),
    ])
    c = make_container(llm)
    with c.session_factory() as db:
        first = c.agent.chat(db, "I'll finish the GRE quant set by Friday")
        assert db.scalars(select(Commitment)).one().status == "proposed"
        assert "?" in first.reply

        second = c.agent.chat(db, "yes", first.conversation_id)
        assert db.scalars(select(Commitment)).one().status == "open"
        assert second.reply == "Got it, that's confirmed."


def test_step_limit_stops_runaway_loops(make_container):
    c = make_container(LoopingLLM(), max_agent_steps=3)
    with c.session_factory() as db:
        res = c.agent.chat(db, "go")
        assert res.reply == STEP_LIMIT_REPLY and len(res.tool_calls) == 3
        last = db.scalars(select(Message).order_by(Message.id.desc())).first()
        assert last.role == "assistant"  # history stays well-formed


def test_history_window_never_starts_mid_tool_exchange(make_container):
    llm = ScriptedLLM([tool_call("list_tasks", {}), text("done")])
    c = make_container(llm, history_message_limit=3)
    with c.session_factory() as db:
        res = c.agent.chat(db, "hi")  # stores 4 messages
        window = c.agent._load_history(db, res.conversation_id)
        # a plain 3-message window would begin with the assistant tool_use; it must be trimmed to start at a user turn
        assert window == [] or (window[0]["role"] == "user" and window[0]["content"][0]["type"] == "text")


def test_truncated_tool_use_is_not_persisted(make_container):
    truncated = LLMResponse(
        [{"type": "text", "text": "Let me"}, {"type": "tool_use", "id": "x", "name": "list_tasks", "input": {}}],
        "max_tokens",
    )
    c = make_container(ScriptedLLM([truncated]))
    with c.session_factory() as db:
        res = c.agent.chat(db, "hi")
        assert res.reply == "Let me"
        stored = db.scalars(select(Message).where(Message.role == "assistant")).one()
        assert all(b["type"] == "text" for b in stored.content)


def test_untrusted_content_in_history_keeps_session_tainted(make_container):
    llm = ScriptedLLM([text("ok"), tool_call("create_task", {"title": "from web page"}), text("queued")])
    c = make_container(llm)
    with c.session_factory() as db:
        first = c.agent.chat(db, "hello")
        db.add(Message(conversation_id=first.conversation_id, role="user",
                       content=[{"type": "tool_result", "tool_use_id": "z",
                                 "content": '<untrusted_content source="web">hi</untrusted_content>'}]))
        db.commit()
        res = c.agent.chat(db, "add what the page said", first.conversation_id)
        assert res.tool_calls[0].status == "pending_confirmation"
        assert db.scalars(select(Task)).all() == []
