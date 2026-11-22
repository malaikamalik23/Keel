"""Memory: durable facts about the user, organised so the agent can find them and the user can trust and correct them.

- kind: preference | context | fact | decision (a choice the user made and why, so it isn't re-litigated later)
- category: personal | academic | professional | project | general
- topic: an optional short name ("university", "supervisor") that turns a memory into a profile entry. There is at most
  one memory per topic: saving a different value for an existing topic is a *conflict*, and nothing is saved until the
  user says which is right. The agent never silently keeps two contradicting facts.
- source and dates: who wrote it (the agent in a clean chat, the agent with the user's approval, or the user), when it
  was last confirmed, and when it was last used. Memories not confirmed for a long time are offered for review.
- lifecycle: confidence (high = the user said or confirmed it, low = inferred), an optional valid_until date, and a
  status. When a fact stops being true (a new job, a move), the old memory becomes "outdated": kept as history with a
  pointer to what replaced it, but never treated as current (left out of each message's context and of searches by
  default). Editing a memory's content keeps the previous wording the same way. Related memories can be linked.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.db.models import Memory, MemoryLink, utcnow
from app.security.untrusted import sanitize_text
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

Kind = Literal["preference", "context", "fact", "decision"]
Category = Literal["personal", "academic", "professional", "project", "general"]
REVIEW_AFTER = timedelta(days=180)  # a memory not confirmed for this long is offered for review
MEANING_POOL = 40  # memories handed to the model when a search finds no shared words
CONTRADICTION_POOL = 8  # other memories in the same category shown when saving, to spot contradictions
_IMPORTANCE = {"high": 0, "medium": 1, "low": 2}
_WORD = re.compile(r"[a-z0-9]{3,}")
# Words too common to say two texts are about the same thing. 3+ letters so "FYP", "GRE" and "CV" still count.
_STOP = {"the", "and", "for", "with", "that", "this", "are", "was", "has", "have", "from", "about", "user", "they",
         "their", "what", "when", "would", "could", "should", "there", "will", "your", "just", "need", "want", "like",
         "some", "them", "then", "you", "how", "can", "did", "does", "not", "but", "all", "any", "know", "tell", "me",
         "my", "its", "our", "who", "why", "now"}
SOURCE_OF_ACTOR = {"agent": "chat", "approved": "approved", "user": "user"}


def _clean(text: str) -> str:
    """Memories are pasted into every later system prompt, so they must never carry invisible characters (a known way to
    hide instructions from the human who approved or reads them)."""
    return sanitize_text(text).strip()


def clean_topic(topic: str | None) -> str | None:
    """A topic is a short label, not text: lower-case words only, so it can't smuggle formatting into the prompt."""
    if topic is None:
        return None
    found = re.findall(r"[a-z0-9]+", sanitize_text(topic).lower())
    return " ".join(found)[:60] or None


def is_current(m: Memory, today: date | None = None) -> bool:
    """True unless it was marked outdated or its valid_until day has passed."""
    if (m.status or "current") != "current":
        return False
    return m.valid_until is None or m.valid_until >= (today or utcnow().date())


def needs_review(m: Memory, now=None) -> bool:
    if not is_current(m, (now or utcnow()).date()):
        return m.status == "current"  # expired but never marked: ask about it
    confirmed = m.confirmed_at or m.updated_at or m.created_at
    return confirmed is not None and (now or utcnow()) - confirmed > REVIEW_AFTER or m.confidence == "low"


def linked_ids(db, memory_id: int) -> list[int]:
    rows = db.scalars(select(MemoryLink).where((MemoryLink.a_id == memory_id) | (MemoryLink.b_id == memory_id)))
    return sorted({r.b_id if r.a_id == memory_id else r.a_id for r in rows})


def memory_dict(m: Memory) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "category": m.category,
        "topic": m.topic,
        "content": m.content,
        "importance": m.importance,
        "source": m.source,
        "created_at": m.created_at.isoformat(timespec="minutes"),
        "confirmed_at": m.confirmed_at.isoformat(timespec="minutes") if m.confirmed_at else None,
        "needs_review": needs_review(m),
        "status": "current" if is_current(m) else "outdated",
        "confidence": m.confidence or "medium",
        **({"valid_until": m.valid_until.isoformat()} if m.valid_until else {}),
        **({"replaced_by": m.replaced_by_id} if m.replaced_by_id else {}),
    }


def _get_memory(ctx: ToolContext, memory_id: int) -> Memory:
    m = ctx.db.get(Memory, memory_id)
    if m is None:
        raise ToolError(f"No memory with id {memory_id}.")
    return m


def _by_topic(ctx: ToolContext, topic: str, exclude_id: int | None = None) -> Memory | None:
    """The current memory for a profile topic (an outdated one is history and doesn't hold the topic)."""
    stmt = select(Memory).where(func.lower(Memory.topic) == topic, Memory.status == "current")
    if exclude_id is not None:
        stmt = stmt.where(Memory.id != exclude_id)
    return ctx.db.scalars(stmt.order_by(Memory.id)).first()


def _base(w: str) -> str:
    """'works' and 'work', 'studies' and 'study' match: questions are worded differently from what was saved."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def words(text: str) -> set[str]:
    return {_base(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def _similar(ctx: ToolContext, content: str, category: str, exclude_id: int | None = None) -> list[Memory]:
    """Memories in the same category that share most of their words with `content` (a possible duplicate or update)."""
    mine = words(content)
    if len(mine) < 3:
        return []
    out = []
    for m in ctx.db.scalars(select(Memory).where(Memory.category == category, Memory.status == "current")):
        if m.id == exclude_id:
            continue
        theirs = words(m.content)
        # Share of the shorter text's words found in the other: an update ("studies best in the late morning now")
        # is usually shorter than what it replaces, which a plain overlap/union ratio would miss.
        if len(theirs) >= 3 and len(mine & theirs) / min(len(mine), len(theirs)) >= 0.6:
            out.append(m)
    return out[:3]


# ---------- save_memory ----------
class SaveMemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=1000, description="One durable fact, stated plainly, e.g. 'Prefers studying in the evening, not mornings.'")
    kind: Kind = Field(
        "fact",
        description="preference = how they like to work/study/communicate. context = background about their life/situation. "
                    "decision = a choice they made and the reason ('Chose NUST over FAST for the lower fees'). fact = other durable info.",
    )
    category: Category = Field("general", description="Which part of their life: personal, academic, professional, project, or general.")
    topic: str | None = Field(
        None, max_length=60,
        description="Only for a single-valued profile fact, a short name for what it is: 'university', 'degree', "
                    "'supervisor', 'career goal', 'home city'. Leave empty for everything else.",
    )
    importance: Literal["low", "medium", "high"] = "medium"
    confidence: Literal["low", "medium", "high"] = Field("medium", description="high if the user stated it plainly; low if you inferred it.")
    valid_until: date | None = Field(None, description="Only if it holds until a known day ('in Lahore until December').")

    @field_validator("topic")
    @classmethod
    def _topic(cls, v):
        return clean_topic(v)


def save_memory(ctx: ToolContext, p: SaveMemoryInput) -> dict:
    content = _clean(p.content)
    if not content:
        raise ToolError("The memory is empty once invisible characters are removed.")
    existing = ctx.db.scalars(select(Memory).where(Memory.content == content, Memory.status == "current")).first()
    if existing:
        return {"memory": memory_dict(existing), "note": "Already stored (duplicate skipped)."}
    if p.topic:
        current = _by_topic(ctx, p.topic)
        if current is not None:
            # Never keep two answers to the same question. The user decides; the agent applies it with update_memory.
            return {
                "saved": False,
                "conflict": {"existing": memory_dict(current), "proposed": content},
                "message": f'Nothing saved. The user\'s {p.topic} is already recorded as "{current.content}" (memory '
                           f"#{current.id}). Ask the user which is correct. If the new one is, call update_memory on "
                           f"#{current.id} with the new content; if the old one still holds, leave it.",
            }
    source = SOURCE_OF_ACTOR.get(ctx.actor, "chat")
    m = Memory(content=content, kind=p.kind, category=p.category, topic=p.topic, importance=p.importance,
               conversation_id=ctx.conversation_id, source=source, confirmed_at=utcnow(),
               confidence="high" if source == "user" else p.confidence, valid_until=p.valid_until)
    ctx.db.add(m)
    ctx.db.flush()
    out: dict = {"memory": memory_dict(m)}
    similar = _similar(ctx, content, p.category, exclude_id=m.id)
    if similar:
        out["similar"] = [memory_dict(s) for s in similar]
        out["note"] = ("Saved. These existing memories look similar: if one of them is now out of date, tell the user and "
                       "offer to update or delete it.")
    # Contradictions worded differently ("lives in Lahore" vs "moved to Islamabad") share few words, so the model
    # gets the other memories in this category to compare by meaning.
    seen = {s.id for s in similar} | {m.id}
    others = [o for o in rank(list(ctx.db.scalars(select(Memory).where(Memory.category == p.category, Memory.status == "current"))), set())
              if o.id not in seen][:CONTRADICTION_POOL]
    if others:
        out["same_category"] = [{"id": o.id, "topic": o.topic, "content": o.content[:160]} for o in others]
        out.setdefault("note", "Saved.")
        out["note"] += (" If the new memory contradicts one in same_category, ask the user which is right; if the old one "
                        "is simply no longer true (a new job, a move), mark it outdated with update_memory(status='outdated', "
                        "replaced_by_id=<new id>) rather than deleting it.")
    return out


# ---------- search_memories ----------
class SearchMemoriesInput(BaseModel):
    query: str | None = Field(None, description="What to look for, in a few words. Omit to list the most important and recent memories.")
    kind: Literal["preference", "context", "fact", "decision", "all"] = "all"
    category: Literal["personal", "academic", "professional", "project", "general", "all"] = "all"
    limit: int = Field(20, ge=1, le=100)
    include_outdated: bool = Field(False, description="Also return facts that are no longer true (history, e.g. 'where did I work before?').")


def rank(memories: list[Memory], query_words: set[str]) -> list[Memory]:
    """Best first: how many query words match (content and topic), then importance, then most recently confirmed."""
    def score(m: Memory):
        hits = len(query_words & (words(m.content) | words(m.topic or ""))) if query_words else 0
        when = m.confirmed_at or m.updated_at or m.created_at
        return (-hits, _IMPORTANCE.get(m.importance, 1), -(when.timestamp() if when else 0), -m.id)
    return sorted(memories, key=score)


def search_memories(ctx: ToolContext, p: SearchMemoriesInput) -> dict:
    stmt = select(Memory)
    if p.kind != "all":
        stmt = stmt.where(Memory.kind == p.kind)
    if p.category != "all":
        stmt = stmt.where(Memory.category == p.category)
    today = ctx.today()
    every = [m for m in ctx.db.scalars(stmt) if p.include_outdated or is_current(m, today)]
    q = words(p.query or "")
    # any query word in the content or topic, so "supervisor email" finds "Dr. Khan is my supervisor"
    rows = [m for m in every if q & (words(m.content) | words(m.topic or ""))] if q else every
    rows = rank(rows, q)[: p.limit]
    now = utcnow()
    for m in rows:
        m.last_used_at = now
    ctx.db.flush()
    out: dict = {"total": len(rows), "memories": [memory_dict(m) for m in rows]}
    links = {m.id: linked_ids(ctx.db, m.id) for m in rows}
    for d in out["memories"]:
        if links.get(d["id"]):
            d["linked"] = links[d["id"]]
    if any(d["status"] == "outdated" for d in out["memories"]):
        out["note_outdated"] = "Memories with status 'outdated' are no longer true: use them only as history."
    if q and not rows and every:
        # No shared words ("where do I study" vs "COMSATS Islamabad, BS CS"): hand the model the memories themselves,
        # compactly, and let it judge by meaning. No extra LLM call, and only on a search that missed.
        pool = rank(every, set())[:MEANING_POOL]
        out["no_word_match"] = True
        out["candidates"] = [{"id": m.id, "category": m.category, "topic": m.topic, "content": m.content[:160]} for m in pool]
        out["note"] = ("No memory shares words with the query. Read these and use the ones that answer it by meaning; "
                       f"if none does, say you don't know.{'' if len(every) <= MEANING_POOL else ' (Only the most important and recent ' + str(MEANING_POOL) + ' are shown.)'}")
    return out


# ---------- review_memories ----------
class ReviewMemoriesInput(BaseModel):
    limit: int = Field(10, ge=1, le=50)


def review_memories(ctx: ToolContext, p: ReviewMemoriesInput) -> dict:
    """Memories that may be out of date (not confirmed for a long time) and topics recorded more than once."""
    now = utcnow()
    every = list(ctx.db.scalars(select(Memory).where(Memory.status == "current").order_by(Memory.id)))
    stale = [m for m in every if needs_review(m, now)]
    by_topic: dict[str, list[Memory]] = {}
    for m in every:
        if m.topic and is_current(m, now.date()):
            by_topic.setdefault(m.topic.lower(), []).append(m)
    conflicts = [{"topic": t, "memories": [memory_dict(m) for m in ms]} for t, ms in by_topic.items() if len(ms) > 1]
    return {
        "stale": [memory_dict(m) for m in stale[: p.limit]],
        "stale_total": len(stale),
        "conflicts": conflicts,
        "how": "Ask the user whether each is still true. Confirm with update_memory(confirm=true), correct it with "
               "update_memory(content=...), or mark it update_memory(status='outdated') if it no longer applies (kept as history).",
    }


# ---------- update_memory ----------
class UpdateMemoryInput(BaseModel):
    memory_id: int
    content: str | None = Field(None, min_length=1, max_length=1000)
    kind: Kind | None = None
    category: Category | None = None
    topic: str | None = Field(None, max_length=60, description="Set or change the profile topic; an empty string removes it.")
    importance: Literal["low", "medium", "high"] | None = None
    confirm: bool = Field(False, description="True when the user has just confirmed this memory is still correct.")
    status: Literal["current", "outdated"] | None = Field(None, description="outdated = no longer true; kept as history, not deleted.")
    replaced_by_id: int | None = Field(None, description="With status=outdated: the memory that replaces it.")
    confidence: Literal["low", "medium", "high"] | None = None
    valid_until: date | None = None

    @field_validator("topic")
    @classmethod
    def _topic(cls, v):
        return "" if v is not None and clean_topic(v) is None else clean_topic(v)


def update_memory(ctx: ToolContext, p: UpdateMemoryInput) -> dict:
    m = _get_memory(ctx, p.memory_id)
    changes = p.model_dump(exclude_unset=True, exclude={"memory_id", "confirm"})
    if not changes and not p.confirm:
        raise ToolError("No fields to update were provided.")
    if changes.get("replaced_by_id") is not None:
        newer = ctx.db.get(Memory, changes["replaced_by_id"])
        if newer is None or newer.id == m.id:
            raise ToolError(f"No other memory with id {changes['replaced_by_id']}.")
        changes.setdefault("status", "outdated")
    if changes.get("status") == "current" and m.topic and _by_topic(ctx, m.topic.lower(), exclude_id=m.id):
        raise ToolError(f'Another memory already holds the topic "{m.topic}". Mark that one outdated first.')
    new_content = _clean(changes["content"]) if changes.get("content") else None
    if new_content and new_content != m.content and changes.get("status") != "outdated":
        # Keep what it used to say, as history: "when information was learned" survives an edit.
        ctx.db.add(Memory(content=m.content, kind=m.kind, category=m.category, topic=None, importance=m.importance,
                          source=m.source, confidence=m.confidence, status="outdated", replaced_by_id=m.id,
                          created_at=m.created_at, confirmed_at=m.confirmed_at, conversation_id=m.conversation_id))
    if changes.get("topic"):
        other = _by_topic(ctx, changes["topic"], exclude_id=m.id)
        if other is not None:
            raise ToolError(f'Topic "{changes["topic"]}" is already used by memory #{other.id} ("{other.content}"). '
                            "Ask the user which one is right, then update or delete the other.")
    for field, value in changes.items():
        if field == "content" and value:
            value = _clean(value)
        if field == "topic":
            value = value or None
        setattr(m, field, value)
    if not m.content:
        raise ToolError("The memory is empty once invisible characters are removed.")
    m.confirmed_at = utcnow()
    if ctx.actor == "user":
        m.source = "user"  # the user's own words now
    ctx.db.flush()
    if p.confirm or (ctx.actor == "user" and changes.get("status") != "outdated"):
        m.confidence = changes.get("confidence") or "high"  # the user just said or confirmed it
    return {"memory": memory_dict(m)}


# ---------- delete_memory (HIGH IMPACT) ----------
class DeleteMemoryInput(BaseModel):
    memory_id: int


def delete_memory(ctx: ToolContext, p: DeleteMemoryInput) -> dict:
    m = _get_memory(ctx, p.memory_id)
    content = m.content
    ctx.db.delete(m)
    ctx.db.flush()
    return {"deleted_memory_id": p.memory_id, "content": content}


def _summarize_save(ctx: ToolContext, p: SaveMemoryInput) -> str:
    # Shown to the human who approves it: exactly the text that will be stored, nothing hidden.
    where = f"{p.category}{', topic ' + p.topic if p.topic else ''}"
    return f'Save a {p.kind} memory ({where}): "{_clean(p.content)}"'


def _summarize_update(ctx: ToolContext, p: UpdateMemoryInput) -> str:
    m = _get_memory(ctx, p.memory_id)
    changes = p.model_dump(exclude_unset=True, exclude={"memory_id", "confirm"})
    if "content" in changes and changes["content"]:
        changes["content"] = _clean(changes["content"])
    parts = [f"{k} -> {v!r}" for k, v in changes.items()] + (["confirm it is still correct"] if p.confirm else [])
    return f'Change memory #{m.id} ("{m.content}"): ' + ", ".join(parts)


def _summarize_delete(ctx: ToolContext, p: DeleteMemoryInput) -> str:
    m = _get_memory(ctx, p.memory_id)
    return f'Permanently delete memory #{m.id}: "{m.content}"'


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="save_memory",
            description=(
                "Save one durable, genuinely useful fact about the user: a preference, personal context, a decision they "
                "made, or other fact worth remembering across conversations. Give it a category. For a single-valued "
                "profile fact (university, degree, supervisor, career goal) also give a topic: if that topic already has "
                "a different value, nothing is saved and you must ask the user which is right. Do not save trivia. Save "
                "proactively when the user reveals something like this, without waiting to be asked."
            ),
            permission=Permission.WRITE,
            input_model=SaveMemoryInput,
            handler=save_memory,
            summarize=_summarize_save,
        )
    )
    registry.register(
        Tool(
            name="search_memories",
            description="Search stored memories (best matches first), optionally by kind or category, or list the most important ones. Use before answering questions about the user's background, preferences, decisions or working style.",
            permission=Permission.READ,
            input_model=SearchMemoriesInput,
            handler=search_memories,
        )
    )
    registry.register(
        Tool(
            name="review_memories",
            description="List memories that may be out of date (not confirmed for months) and profile topics stored twice, so you can check them with the user.",
            permission=Permission.READ,
            input_model=ReviewMemoriesInput,
            handler=review_memories,
        )
    )
    registry.register(
        Tool(
            name="update_memory",
            description="Edit a memory, confirm it is still correct (confirm=true), or mark it no longer true (status='outdated', "
                        "replaced_by_id=<the new fact>): outdated facts are kept as history, never treated as current.",
            permission=Permission.WRITE,
            input_model=UpdateMemoryInput,
            handler=update_memory,
            summarize=_summarize_update,
        )
    )
    registry.register(
        Tool(
            name="delete_memory",
            description="Permanently delete a memory. High impact: queued for the user's approval.",
            permission=Permission.HIGH_IMPACT,
            input_model=DeleteMemoryInput,
            handler=delete_memory,
            summarize=_summarize_delete,
        )
    )
