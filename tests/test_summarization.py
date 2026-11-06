from app.agent.summarize import render_messages_for_summary, summarize_messages
from app.db.models import Conversation, Message
from tests.fakes import ScriptedLLM, text


class _Row:
    """Minimal stand-in for a Message row, since render_messages_for_summary only reads role/content."""

    def __init__(self, role, content):
        self.role = role
        self.content = content


def test_render_messages_for_summary_covers_all_block_types():
    rows = [
        _Row("user", [{"type": "text", "text": "I'll finish the report by Friday"}]),
        _Row("assistant", [{"type": "tool_use", "id": "x", "name": "propose_commitment", "input": {"text": "report"}}]),
        _Row("user", [{"type": "tool_result", "tool_use_id": "x", "content": '{"status": "ok"}'}]),
        _Row("assistant", [{"type": "text", "text": "Got it, tracked."}]),
    ]
    rendered = render_messages_for_summary(rows)
    assert "I'll finish the report by Friday" in rendered
    assert "propose_commitment" in rendered
    assert "tool result" in rendered
    assert "Got it, tracked." in rendered


def test_summarize_messages_returns_prior_summary_when_excerpt_is_empty():
    llm = ScriptedLLM([])  # must not be called at all
    assert summarize_messages(llm, "existing notes", "") == "existing notes"
    assert llm.calls == []


def test_summarize_messages_falls_back_to_prior_on_blank_response():
    llm = ScriptedLLM([text("   ")])
    assert summarize_messages(llm, "existing notes", "something happened") == "existing notes"


def test_short_conversation_is_not_summarized(make_container):
    """Well within history_message_limit: no extra LLM call, no summary stored."""
    llm = ScriptedLLM([text("hi there"), text("still here")])
    c = make_container(llm, history_message_limit=40)
    with c.session_factory() as db:
        first = c.agent.chat(db, "hello")
        c.agent.chat(db, "you there?", first.conversation_id)
        conv = db.get(Conversation, first.conversation_id)
        assert conv.summary is None
    assert len(llm.calls) == 2  # no summarization call was made


def test_long_conversation_folds_dropped_messages_into_summary(make_container):
    """With a small window, older turns must be summarized instead of vanishing outright.

    `history_message_limit=4` only starts dropping messages once more than 4 are already stored
    for the tracked conversation, i.e. once its 3rd exchange is about to be persisted. The
    "turn1" call below is a throwaway conversation (exercises the None-conversation_id path and
    keeps ids offset, matching real usage); the tracked conversation starts at "turn2". Windowing
    is FIFO, so the *earliest* tracked exchange ("turn2", carrying the Nightingale mention) is the
    one that falls out of the window and gets summarized -- not whichever exchange mentions it.
    """
    llm = ScriptedLLM([
        text("reply 1"),
        text("reply about Nightingale"),
        text("reply 3"),
        text("reply 4"),
        text("- user mentioned project Nightingale"),  # the summarization call, triggered by turn5's load
        text("reply 5"),
    ])
    c = make_container(llm, history_message_limit=4)
    with c.session_factory() as db:
        c.agent.chat(db, "turn1")
        conv_id = c.agent.chat(db, "turn2 (about project Nightingale)").conversation_id
        c.agent.chat(db, "turn3", conv_id)
        c.agent.chat(db, "turn4", conv_id)
        fifth = c.agent.chat(db, "turn5", conv_id)

        conv = db.get(Conversation, conv_id)
        assert conv.summary == "- user mentioned project Nightingale"
        assert conv.summarized_through_id == 4  # turn2's user+assistant messages

    # the 6th LLM call (turn5's actual reply) must have received the summary in its system prompt
    turn5_call = llm.calls[5]
    assert "Nightingale" in turn5_call["system"]
    assert "no longer replayed in full" in turn5_call["system"]
    assert fifth.reply == "reply 5"


def test_summarization_failure_does_not_break_the_turn(make_container):
    """If the summarizer call itself fails, the main turn must still complete normally."""

    class FlakyThenFineLLM:
        def __init__(self, main_responses):
            self.main_responses = list(main_responses)
            self.calls = []
            self._n = 0

        def complete(self, *, system, messages, tools):
            self.calls.append({"system": system, "messages": messages, "tools": tools})
            self._n += 1
            # Every call whose only message is the synthetic summarization prompt fails.
            if len(messages) == 1 and "Existing summary" in messages[0]["content"][0]["text"]:
                raise RuntimeError("summarizer is down")
            return self.main_responses.pop(0)

    llm = FlakyThenFineLLM([text("reply 1"), text("reply 2"), text("reply 3"), text("reply 4")])
    c = make_container(llm, history_message_limit=4)
    with c.session_factory() as db:
        c.agent.chat(db, "turn1")
        conv_id = c.agent.chat(db, "turn2").conversation_id
        c.agent.chat(db, "turn3", conv_id)
        fourth = c.agent.chat(db, "turn4", conv_id)  # would trigger summarization, which fails

    assert fourth.reply == "reply 4"
    with c.session_factory() as db:
        conv = db.get(Conversation, conv_id)
        assert conv.summary is None  # failed summarization left it untouched, not corrupted


def test_summarize_dropped_history_false_restores_old_truncate_only_behavior(make_container):
    llm = ScriptedLLM([text("r1"), text("r2"), text("r3"), text("r4")])
    c = make_container(llm, history_message_limit=4, summarize_dropped_history=False)
    with c.session_factory() as db:
        c.agent.chat(db, "turn1")
        conv_id = c.agent.chat(db, "turn2").conversation_id
        c.agent.chat(db, "turn3", conv_id)
        c.agent.chat(db, "turn4", conv_id)
        conv = db.get(Conversation, conv_id)
        assert conv.summary is None
    assert len(llm.calls) == 4  # never made an extra summarization call
