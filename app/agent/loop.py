"""The agent: one conversation turn = a bounded loop of (LLM call -> run requested tools -> LLM call ...)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.context import build_context_pack
from app.agent.prompts import COMPOSE_SYSTEM, build_system_prompt
from app.agent.summarize import render_messages_for_summary, summarize_messages
from app.config import Settings
from app.db.models import Conversation, Message
from app.errors import NotFoundError
from app.llm.base import LLMClient
from app.security.untrusted import contains_untrusted, extract_urls, urls_in_untrusted_result
from app.tools.base import ToolContext
from app.tools.executor import ToolExecutor
from app.tools.groups import not_loaded, recent_groups, select_groups

logger = logging.getLogger(__name__)

STEP_LIMIT_REPLY = (
    "I hit my step limit while working on that, so I stopped. Tell me what you'd like me to focus on and I'll continue."
)


@dataclass
class ToolCallRecord:
    name: str
    permission: str
    status: str
    input: dict
    result: dict


@dataclass
class ChatResult:
    conversation_id: int
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    pending_action_ids: list[int] = field(default_factory=list)


def _is_plain_user_message(msg: dict) -> bool:
    if msg["role"] != "user":
        return False
    content = msg["content"]
    return isinstance(content, str) or not any(b.get("type") == "tool_result" for b in content)


class Agent:
    def __init__(self, llm: LLMClient, executor: ToolExecutor, settings: Settings):
        self.llm = llm
        self.executor = executor
        self.settings = settings
        self.tz = ZoneInfo(settings.agent_timezone)

    # ---------- public ----------
    def chat(self, db: Session, message: str, conversation_id: int | None = None) -> ChatResult:
        self.executor.expire_stale(db)  # housekeeping: pending actions no one confirmed in time
        conv = self._get_or_create_conversation(db, conversation_id)
        history = self._load_history(db, conv.id)  # may update conv.summary as a side effect
        user_msg = {"role": "user", "content": [{"type": "text", "text": message}]}
        self._persist(db, conv.id, [user_msg])

        messages = history + [user_msg]
        ctx = ToolContext(db=db, tz=self.tz, conversation_id=conv.id)
        # If untrusted content is still in the context window, stay in the stricter permission mode.
        # Once a conversation has read external content it stays tainted: the page may be gone from the window, but the
        # summary or the agent's own replies can still repeat it. Start a new conversation for a clean session.
        ctx.tainted = bool(conv.tainted) or contains_untrusted(messages)
        ctx.seen_urls = extract_urls(messages)
        context = build_context_pack(db, ctx.today(), message)

        registry = self.executor.registry
        lean = self.settings.tool_profile == "lean"
        available = registry.available_groups()
        if lean:  # advertise only the tools this message plausibly needs (see app/tools/groups.py)
            ctx.active_groups = select_groups(message, recent_groups(history), available)

        result = ChatResult(conversation_id=conv.id, reply="")
        for _ in range(self.settings.max_agent_steps):
            # Rebuilt each step: a load_tools call during this turn changes which tools and which catalog entries apply.
            system_prompt = build_system_prompt(
                ctx.now(), summary=conv.summary, summary_untrusted=bool(conv.tainted), context=context, more_tools=not_loaded(ctx.active_groups, available) if lean else None
            )
            response = self.llm.complete(
                system=system_prompt,
                messages=messages,
                tools=registry.specs(ctx.active_groups) if lean else registry.specs(),
            )
            tool_uses = response.tool_uses if response.stop_reason == "tool_use" else []

            if not tool_uses:
                # Drop any dangling tool_use (e.g. truncated output): an unanswered tool_use would corrupt history.
                content = [b for b in response.content if b["type"] == "text" and b["text"]]
                content = content or [{"type": "text", "text": "(no response)"}]
                self._persist(db, conv.id, [{"role": "assistant", "content": content}])
                result.reply = response.text or "(no response)"
                return result

            tool_results = []
            for tu in tool_uses:
                outcome = self.executor.run(ctx, tu["name"], tu["input"])
                result.tool_calls.append(
                    ToolCallRecord(tu["name"], outcome.permission, outcome.status, tu["input"], outcome.result)
                )
                if outcome.pending_action_id is not None:
                    result.pending_action_ids.append(outcome.pending_action_id)
                ctx.seen_urls |= urls_in_untrusted_result(outcome.result)  # links the model has now been shown
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": json.dumps(outcome.result, default=str, ensure_ascii=False),
                        "is_error": outcome.status == "error",
                    }
                )
            # The assistant tool_use message and its tool_result reply are saved together, never one without the other.
            new = [{"role": "assistant", "content": response.content}, {"role": "user", "content": tool_results}]
            self._persist(db, conv.id, new)
            messages += new

        self._persist(db, conv.id, [{"role": "assistant", "content": [{"type": "text", "text": STEP_LIMIT_REPLY}]}])
        result.reply = STEP_LIMIT_REPLY
        return result

    def compose_message(self, kind: str, facts: dict, fallback: str) -> str:
        """Word a proactive message (check-in or nudge). One LLM call with NO tools: it cannot change anything.

        The facts come from SQL, not from the model. If the LLM is down or returns nothing, `fallback` (a
        deterministic text built from the same facts) is sent instead, so a flaky provider never silences a check-in.
        """
        try:
            response = self.llm.complete(
                system=COMPOSE_SYSTEM,
                messages=[{"role": "user", "content": [{"type": "text", "text": f"Message type: {kind}\nFacts (JSON, computed by code):\n{json.dumps(facts, default=str)}"}]}],
                tools=[],
            )
            text = response.text.strip()
            if text:
                return text[:1500]
        except Exception:  # noqa: BLE001 - proactive messages must never fail because the LLM did
            logger.exception("compose_message failed for %s; using fallback", kind)
        return fallback

    # ---------- helpers ----------
    def _get_or_create_conversation(self, db: Session, conversation_id: int | None) -> Conversation:
        if conversation_id is None:
            conv = Conversation()
            db.add(conv)
            db.commit()
            return conv
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            raise NotFoundError(f"No conversation with id {conversation_id}.")
        return conv

    def _load_history(self, db: Session, conversation_id: int) -> list[dict]:
        rows = db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.desc())
            .limit(self.settings.history_message_limit)
        ).all()
        rows = list(reversed(rows))
        # Windowing must not cut a tool_use / tool_result pair in half: start at a plain user message.
        while rows and not _is_plain_user_message({"role": rows[0].role, "content": rows[0].content}):
            rows.pop(0)

        if self.settings.summarize_dropped_history:
            conv = db.get(Conversation, conversation_id)
            if conv is not None:
                self._maybe_summarize(db, conv, rows[0].id if rows else None)

        return [{"role": r.role, "content": r.content} for r in rows]

    def _maybe_summarize(self, db: Session, conv: Conversation, window_start_id: int | None) -> None:
        """Fold messages that just fell out of the replayed window into `conv.summary`.

        Runs incrementally: only the slice between the last summarized message and the new window
        start is summarized each turn, not the whole history every time.
        """
        if window_start_id is None:
            return
        already_through = conv.summarized_through_id or 0
        if window_start_id - 1 <= already_through:
            return  # nothing new has aged out of the window since we last summarized

        dropped = db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conv.id,
                Message.id > already_through,
                Message.id < window_start_id,
            )
            .order_by(Message.id)
        ).all()
        if not dropped:
            return

        excerpt = render_messages_for_summary(dropped)
        if contains_untrusted([{"content": r.content} for r in dropped]) and not conv.tainted:
            conv.tainted = True  # before summarizing, so a failed summarizer call can't lose it
            db.commit()
        try:
            conv.summary = summarize_messages(self.llm, conv.summary, excerpt)
        except Exception:
            # Summarization is a nice-to-have; a flaky/unconfigured LLM must not break the chat turn.
            logger.exception("Conversation summarization failed for conversation %s; keeping prior summary", conv.id)
            return
        conv.summarized_through_id = dropped[-1].id
        db.commit()

    def _persist(self, db: Session, conversation_id: int, msgs: list[dict]) -> None:
        for m in msgs:
            db.add(Message(conversation_id=conversation_id, role=m["role"], content=m["content"]))
        if contains_untrusted(msgs):  # saved in the same commit as the external content itself
            conv = db.get(Conversation, conversation_id)
            if conv is not None:
                conv.tainted = True
        db.commit()
