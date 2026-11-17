"""Context pack: a compact, code-assembled snapshot injected into the system prompt each turn.

The model gets the essentials (what's due, what's slipping, how the user likes to work, memories relevant
to the message) without having to remember to call a tool first. Everything here is a plain SQL query --
no LLM involved -- and the block is size-capped so it can't crowd out the conversation.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Memory, Notification, Plan, Task, utcnow
from app.security.untrusted import sanitize_text
from app.tools.memory import rank
from app.tools.memory import words as memory_words

MAX_TASKS = 5
MAX_COMMITMENTS = 5
MAX_PROFILE = 10
MAX_PREFERENCES = 8
MAX_RELEVANT_MEMORIES = 5
MAX_CHARS = 3000
PROACTIVE_WINDOW = timedelta(hours=12)
AT_RISK_DAYS = 2  # a commitment due within this many days (or already past due) is flagged

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _clip(text: str, n: int = 140) -> str:
    # One line, no invisible characters: stored text (memories, titles) must not be able to fake a new section of the
    # system prompt or hide instructions from the user who can see the same data in the dashboard.
    text = " ".join(sanitize_text(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _task_line(t: Task, today: date) -> str:
    parts = [f"#{t.id} {_clip(t.title, 80)}", f"priority {t.priority}"]
    if t.due_date:
        parts.append(f"due {t.due_date.isoformat()}" + (" (OVERDUE)" if t.due_date < today else ""))
    if t.postponement_count:
        parts.append(f"postponed {t.postponement_count}x")
    return "- " + ", ".join(parts)


def _commitment_line(c: Commitment, today: date) -> str:
    parts = [f"#{c.id} {_clip(c.text, 100)}"]
    if c.deadline:
        parts.append(f"due {c.deadline.isoformat()}" + (" (OVERDUE)" if c.deadline < today else ""))
    if c.postponement_count:
        parts.append(f"postponed {c.postponement_count}x")
    return "- " + ", ".join(parts)


def build_context_pack(db: Session, today: date, message: str = "") -> str:
    """Return a markdown block for the system prompt, or "" when there is nothing worth showing."""
    sections: list[str] = []

    # What the agent itself last said unprompted (check-in / nudge): a reply like "didn't get to it" needs this.
    last = db.scalars(
        select(Notification).where(Notification.status == "sent", Notification.kind != "reminder").order_by(Notification.id.desc())
    ).first()
    if last is not None:
        age = utcnow() - last.sent_at
        if timedelta(0) <= age <= PROACTIVE_WINDOW:
            hours = max(int(age.total_seconds() // 3600), 0)
            sections.append(f"Your most recent proactive message to the user ({last.kind}, {hours}h ago) -- they may be replying to it:\n\"{_clip(last.text, 400)}\"")

    # Today's plan, if one exists.
    plan = db.scalars(select(Plan).where(Plan.plan_date == today)).first()
    if plan and plan.items:
        lines = [f"- [{i.status}] #{i.id} {_clip(i.title, 80)} ({i.planned_minutes} min{', major' if i.is_major else ''})" for i in plan.items]
        sections.append(f"Today's plan ({plan.capacity_minutes} min capacity):\n" + "\n".join(lines))

    # Open tasks: overdue/soonest first, then by priority.
    tasks = list(db.scalars(select(Task).where(Task.status.in_(("todo", "in_progress")))))
    tasks.sort(key=lambda t: (t.due_date is None, t.due_date or date.max, _PRIORITY_RANK.get(t.priority, 1), t.id))
    if tasks:
        sections.append("Open tasks (soonest first):\n" + "\n".join(_task_line(t, today) for t in tasks[:MAX_TASKS]))
        if len(tasks) > MAX_TASKS:
            sections[-1] += f"\n(+{len(tasks) - MAX_TASKS} more; use list_tasks)"

    # Commitments that are overdue or due very soon.
    horizon = today + timedelta(days=AT_RISK_DAYS)
    open_commitments = list(db.scalars(select(Commitment).where(Commitment.status == "open")))
    at_risk = [c for c in open_commitments if c.deadline and c.deadline <= horizon]
    at_risk.sort(key=lambda c: (c.deadline, c.id))
    if at_risk:
        sections.append(
            "Commitments overdue or due within "
            f"{AT_RISK_DAYS} days:\n" + "\n".join(_commitment_line(c, today) for c in at_risk[:MAX_COMMITMENTS])
        )

    # Things that keep slipping (the accountability signal).
    slipping = [f"- task {_task_line(t, today)[2:]}" for t in tasks if t.postponement_count >= 2]
    slipping += [f"- commitment {_commitment_line(c, today)[2:]}" for c in open_commitments if c.postponement_count >= 2]
    if slipping:
        sections.append("Repeatedly postponed (ask why, don't just reschedule):\n" + "\n".join(slipping[:5]))

    proposed = db.scalars(select(Commitment).where(Commitment.status == "proposed")).all()
    if proposed:
        sections.append(
            "Proposed commitments awaiting the user's confirmation:\n"
            + "\n".join(_commitment_line(c, today) for c in proposed[:3])
        )

    # The profile (memories with a topic: university, supervisor, career goal...) and preferences always apply; other
    # memories only when the message touches them, best match first. Everything shown is marked as used.
    memories = list(db.scalars(select(Memory)))
    shown: list[Memory] = []
    profile = rank([m for m in memories if m.topic], set())[:MAX_PROFILE]
    if profile:
        profile.sort(key=lambda m: (m.category, m.topic or ""))
        sections.append("User profile (from stored memories):\n" + "\n".join(
            f"- (#{m.id}) {m.category} / {_clip(m.topic or '', 40)}: {_clip(m.content)}" for m in profile))
        shown += profile
    prefs = rank([m for m in memories if m.kind == "preference" and not m.topic], set())[:MAX_PREFERENCES]
    if prefs:
        sections.append("User preferences:\n" + "\n".join(f"- (#{m.id}) {_clip(m.content)}" for m in prefs))
        shown += prefs

    query = memory_words(message)
    if query:
        taken = {m.id for m in shown}
        candidates = [m for m in memories if m.id not in taken and query & (memory_words(m.content) | memory_words(m.topic or ""))]
        relevant = rank(candidates, query)[:MAX_RELEVANT_MEMORIES]
        if relevant:
            sections.append(
                "Stored memories relevant to this message:\n"
                + "\n".join(f"- (#{m.id}, {m.kind}, {m.category}) {_clip(m.content)}" for m in relevant)
            )
            shown += relevant
    if shown:
        now = utcnow()
        for m in shown:
            m.last_used_at = now

    if not sections:
        return ""
    block = "\n\n".join(sections)
    return block if len(block) <= MAX_CHARS else block[: MAX_CHARS - 1].rstrip() + "…"
