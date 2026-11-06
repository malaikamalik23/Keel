from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Memory
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


def _memory_dict(m: Memory) -> dict:
    return {
        "id": m.id,
        "kind": m.kind,
        "content": m.content,
        "importance": m.importance,
        "created_at": m.created_at.isoformat(timespec="minutes"),
    }


def _get_memory(ctx: ToolContext, memory_id: int) -> Memory:
    m = ctx.db.get(Memory, memory_id)
    if m is None:
        raise ToolError(f"No memory with id {memory_id}.")
    return m


# ---------- save_memory ----------
class SaveMemoryInput(BaseModel):
    content: str = Field(min_length=1, max_length=1000, description="One durable fact, stated plainly, e.g. 'Prefers studying in the evening, not mornings.'")
    kind: Literal["preference", "context", "fact"] = Field(
        "fact", description="preference = how they like to work/study/communicate. context = background about their life/situation. fact = other durable info."
    )
    importance: Literal["low", "medium", "high"] = "medium"


def save_memory(ctx: ToolContext, p: SaveMemoryInput) -> dict:
    content = p.content.strip()
    existing = ctx.db.scalars(select(Memory).where(Memory.content == content)).first()
    if existing:
        return {"memory": _memory_dict(existing), "note": "Already stored (duplicate skipped)."}
    m = Memory(content=content, kind=p.kind, importance=p.importance, conversation_id=ctx.conversation_id)
    ctx.db.add(m)
    ctx.db.flush()
    return {"memory": _memory_dict(m)}


# ---------- search_memories ----------
class SearchMemoriesInput(BaseModel):
    query: str | None = Field(None, description="Keyword(s) to search for. Omit to list recent memories.")
    kind: Literal["preference", "context", "fact", "all"] = "all"
    limit: int = Field(20, ge=1, le=100)


def search_memories(ctx: ToolContext, p: SearchMemoriesInput) -> dict:
    stmt = select(Memory)
    if p.kind != "all":
        stmt = stmt.where(Memory.kind == p.kind)
    if p.query:
        stmt = stmt.where(Memory.content.ilike(f"%{p.query.strip()}%"))
    rows = list(ctx.db.scalars(stmt.order_by(Memory.updated_at.desc()).limit(p.limit)))
    return {"total": len(rows), "memories": [_memory_dict(m) for m in rows]}


# ---------- update_memory ----------
class UpdateMemoryInput(BaseModel):
    memory_id: int
    content: str | None = Field(None, min_length=1, max_length=1000)
    kind: Literal["preference", "context", "fact"] | None = None
    importance: Literal["low", "medium", "high"] | None = None


def update_memory(ctx: ToolContext, p: UpdateMemoryInput) -> dict:
    m = _get_memory(ctx, p.memory_id)
    changes = p.model_dump(exclude_unset=True, exclude={"memory_id"})
    if not changes:
        raise ToolError("No fields to update were provided.")
    for field, value in changes.items():
        setattr(m, field, value.strip() if field == "content" and value else value)
    ctx.db.flush()
    return {"memory": _memory_dict(m)}


# ---------- delete_memory (HIGH IMPACT) ----------
class DeleteMemoryInput(BaseModel):
    memory_id: int


def delete_memory(ctx: ToolContext, p: DeleteMemoryInput) -> dict:
    m = _get_memory(ctx, p.memory_id)
    content = m.content
    ctx.db.delete(m)
    ctx.db.flush()
    return {"deleted_memory_id": p.memory_id, "content": content}


def _summarize_delete(ctx: ToolContext, p: DeleteMemoryInput) -> str:
    m = _get_memory(ctx, p.memory_id)
    return f'Permanently delete memory #{m.id}: "{m.content}"'


def register(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="save_memory",
            description=(
                "Save one durable, genuinely useful fact about the user: a preference, personal context, or other "
                "fact worth remembering across conversations. Do not save trivia or one-off details. Save proactively "
                "when the user reveals something like this in conversation, without waiting to be asked."
            ),
            permission=Permission.WRITE,
            input_model=SaveMemoryInput,
            handler=save_memory,
        )
    )
    registry.register(
        Tool(
            name="search_memories",
            description="Search stored memories by keyword, or list recent ones. Use before answering questions about the user's preferences, background, or working style.",
            permission=Permission.READ,
            input_model=SearchMemoriesInput,
            handler=search_memories,
        )
    )
    registry.register(
        Tool(
            name="update_memory",
            description="Edit an existing memory's content, kind, or importance.",
            permission=Permission.WRITE,
            input_model=UpdateMemoryInput,
            handler=update_memory,
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
