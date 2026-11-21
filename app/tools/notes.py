from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Document
from app.services.doc_analysis import compare, extract_items, outline
from app.services.documents import NotesUnavailable, get_document, read_indexed_note, search_documents
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

PATH_HELP = "A path from notes_search, or a file name like 'proposal.docx'."


class NotesSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Words to look for in the user's own notes/papers.")
    limit: int = Field(5, ge=1, le=15)


class ReadNoteInput(BaseModel):
    path: str = Field(max_length=500, description=PATH_HELP)
    page: int | None = Field(None, ge=1, description="Read only this page.")
    start_chunk: int = Field(0, ge=0, description="Continue a long document from next_start_chunk.")


class PathInput(BaseModel):
    path: str = Field(max_length=500, description=PATH_HELP)


class ExtractInput(PathInput):
    find: Literal["deadlines", "action_items", "both"] = "both"


class CompareInput(BaseModel):
    path_a: str = Field(max_length=500, description="The older version / first document.")
    path_b: str = Field(max_length=500, description="The newer version / second document.")


def find_document(ctx: ToolContext, path: str) -> Document:
    """Only indexed documents can be read (never the disk). Accepts the exact path or an unambiguous file name."""
    doc = get_document(ctx.db, path)
    if doc is not None:
        return doc
    want = path.strip().replace("\\", "/").casefold()
    docs = list(ctx.db.scalars(select(Document)))
    hits = [d for d in docs if d.path.casefold().rsplit("/", 1)[-1] == want or d.path.casefold().rsplit("/", 1)[-1].rsplit(".", 1)[0] == want]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ToolError("Several documents match: " + ", ".join(d.path for d in hits[:8]) + ". Pass the full path.")
    raise ToolError("No indexed document with that path. Use notes_search to find it.")


def notes_search(ctx: ToolContext, p: NotesSearchInput) -> dict:
    try:
        results = search_documents(ctx.db, p.query, p.limit)
    except NotesUnavailable as exc:
        raise ToolError(str(exc)) from None
    return {"query": p.query, "results": results}


def read_note(ctx: ToolContext, p: ReadNoteInput) -> dict:
    doc = find_document(ctx, p.path)
    out = read_indexed_note(ctx.db, doc.path, page=p.page, start_chunk=p.start_chunk)
    if p.page is not None and not out["text"]:
        raise ToolError(f"Page {p.page} has no text" + (f" (the document has {doc.pages} pages)." if doc.pages else " or the document has no page numbers."))
    return out


def note_outline(ctx: ToolContext, p: PathInput) -> dict:
    return outline(find_document(ctx, p.path))


def extract_from_note(ctx: ToolContext, p: ExtractInput) -> dict:
    out = extract_items(find_document(ctx, p.path), ctx.today())
    if p.find == "deadlines":
        out.pop("action_items")
    elif p.find == "action_items":
        out.pop("deadlines")
        out.pop("other_dates")
    return out


def compare_notes(ctx: ToolContext, p: CompareInput) -> dict:
    a, b = find_document(ctx, p.path_a), find_document(ctx, p.path_b)
    if a.id == b.id:
        raise ToolError("That is the same document twice.")
    return compare(a, b)


def register(registry: ToolRegistry) -> None:
    # Notes can contain pasted web text or downloaded PDFs, so everything that returns their text is fenced as untrusted.
    for name, desc, model, handler in (
        ("notes_search", "Full-text search over the user's own notes, papers and documents (PDF, Word, text; Urdu too). Use for "
         "'what did I write about X' or 'find my notes on Y'. Returns snippets with paths and page numbers.", NotesSearchInput, notes_search),
        ("read_note", "Read one document's text, with [page N] marks: cite pages as (file, p. N), or p. ~N if estimated. Long documents come in parts "
         "(next_start_chunk); to summarise, read all parts first.", ReadNoteInput, read_note),
        ("note_outline", "A document's headings with their pages, page and word count.", PathInput, note_outline),
        ("extract_from_note", "Find deadlines, dates and action items in a document, each with its page.", ExtractInput, extract_from_note),
        ("compare_notes", "Compare two documents or two versions of one: what was added, removed or changed.", CompareInput, compare_notes),
    ):
        registry.register(Tool(name=name, description=desc, permission=Permission.READ, input_model=model, handler=handler, returns_untrusted=True))
