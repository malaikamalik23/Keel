from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.documents import NotesUnavailable, read_indexed_note, search_documents
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry


class NotesSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Words to look for in the user's own notes/papers.")
    limit: int = Field(5, ge=1, le=15)


class ReadNoteInput(BaseModel):
    path: str = Field(max_length=500, description="A path exactly as returned by notes_search.")


def notes_search(ctx: ToolContext, p: NotesSearchInput) -> dict:
    try:
        results = search_documents(ctx.db, p.query, p.limit)
    except NotesUnavailable as exc:
        raise ToolError(str(exc)) from None
    return {"query": p.query, "results": results}


def read_note(ctx: ToolContext, p: ReadNoteInput) -> dict:
    out = read_indexed_note(ctx.db, p.path)
    if not out:
        raise ToolError("No indexed note with that path. Use notes_search to find paths.")
    return out


def register(registry: ToolRegistry) -> None:
    # Notes can contain pasted web text or downloaded PDFs, so they are fenced as untrusted like any external source.
    registry.register(Tool(
        name="notes_search",
        description="Full-text search over the user's own notes, papers and documents (works for Urdu too). Use for 'what did I write about X' or 'find my notes on Y'. Returns snippets with file paths.",
        permission=Permission.READ, input_model=NotesSearchInput, handler=notes_search, returns_untrusted=True,
    ))
    registry.register(Tool(
        name="read_note",
        description="Read the indexed text of one note by the path returned from notes_search.",
        permission=Permission.READ, input_model=ReadNoteInput, handler=read_note, returns_untrusted=True,
    ))
