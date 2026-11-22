"""File management for the notes folder: link documents to applications, tasks, emails and projects; move or rename
them; create and edit notes.

Safety:
* Everything stays inside NOTES_DIR (paths are resolved and checked; no symlink escapes, no hidden folders).
* Nothing is overwritten: a move or a new note never replaces an existing file.
* Moving a file is high impact (other apps may point at it), so the user approves the exact from -> to.
* Editing a note is a write, limited to text notes (.md, .txt): the change must match exactly one place, a copy of
  the old version is kept in NOTES_DIR/.keel-backups/, and once untrusted text has been read the user approves it.
* Links are kept by path, so they survive re-indexing; Keel updates them when it moves a file.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.db.models import Document, DocumentLink, Opportunity, ProjectLink, Task
from app.security.untrusted import sanitize_text
from app.tools.base import Permission, Tool, ToolContext, ToolError, ToolRegistry

TEXT_NOTES = (".md", ".txt")
BACKUPS = ".keel-backups"
MAX_NOTE_BYTES = 500_000


class Target(BaseModel):
    opportunity_id: int | None = Field(None, description="An application/scholarship/job being tracked.")
    task_id: int | None = None
    project_id: int | None = None
    email_message_id: str | None = Field(None, max_length=64, description="A Gmail message id (from gmail_search).")

    @model_validator(mode="after")
    def _one(self):
        if sum(v is not None for v in (self.opportunity_id, self.task_id, self.project_id, self.email_message_id)) != 1:
            raise ValueError("Give exactly one of opportunity_id, task_id, project_id or email_message_id.")
        return self


class LinkInput(Target):
    path: str = Field(max_length=500, description="An indexed document: its path or unique file name.")
    note: str | None = Field(None, max_length=300, description="Why it belongs there, e.g. 'final transcript'.")


class PathInput(BaseModel):
    path: str = Field(max_length=500)


class MoveInput(BaseModel):
    path: str = Field(max_length=500)
    new_path: str = Field(max_length=500, description="New place inside the notes folder, e.g. 'applications/daad/transcript.pdf'.")


class CreateNoteInput(BaseModel):
    path: str = Field(max_length=500, description="New .md or .txt file inside the notes folder.")
    content: str = Field(min_length=1, max_length=200_000)


class EditNoteInput(BaseModel):
    path: str = Field(max_length=500)
    find: str | None = Field(None, max_length=5000, description="Exact text to replace; must appear exactly once.")
    replace: str | None = Field(None, max_length=20000)
    append: str | None = Field(None, max_length=20000, description="Text to add at the end instead.")

    @model_validator(mode="after")
    def _one(self):
        if (self.append is None) == (self.find is None):
            raise ValueError("Either append text, or find and replace.")
        if self.find is not None and self.replace is None:
            raise ValueError("Say what to replace it with (replace).")
        return self


def register(registry: ToolRegistry, notes_dir: str, *, gmail=None) -> None:
    root = Path(notes_dir).expanduser().resolve()

    def _inside(rel: str, *, must_exist: bool) -> tuple[Path, str]:
        rel = rel.strip().replace("\\", "/").lstrip("/")
        full = (root / rel).resolve()
        if not full.is_relative_to(root) or full == root:
            raise ToolError("That path is outside the notes folder.")
        parts = full.relative_to(root).parts
        if any(p.startswith(".") for p in parts):
            raise ToolError("Hidden files and folders are off limits.")
        if must_exist and not full.is_file():
            raise ToolError(f"No file at {rel} in the notes folder.")
        return full, full.relative_to(root).as_posix()

    def _doc(ctx: ToolContext, path: str) -> Document:
        from app.tools.notes import find_document

        return find_document(ctx, path)

    def _reindex(ctx: ToolContext) -> None:
        from app.services.documents import index_directory

        index_directory(ctx.db, root)

    # ---- links
    def _describe_target(ctx: ToolContext, t: Target) -> tuple[str, str, str]:
        """(target_type, target_id, title)"""
        if t.opportunity_id is not None:
            o = ctx.db.get(Opportunity, t.opportunity_id)
            if o is None:
                raise ToolError(f"No application with id {t.opportunity_id}.")
            return "opportunity", str(o.id), o.title
        if t.task_id is not None:
            task = ctx.db.get(Task, t.task_id)
            if task is None:
                raise ToolError(f"No task with id {t.task_id}.")
            return "task", str(task.id), task.title
        if t.project_id is not None:
            from app.db.models import Project

            proj = ctx.db.get(Project, t.project_id)
            if proj is None:
                raise ToolError(f"No project with id {t.project_id}.")
            return "project", str(proj.id), proj.name
        mid = t.email_message_id.strip()
        title = None
        if gmail is not None:
            try:
                m = gmail.message(mid)
                title = f"{m.subject} (from {m.sender})"[:200]
            except Exception:  # noqa: BLE001 - linking still works without the subject
                title = None
        return "email", mid, title or f"email {mid}"

    def link_document(ctx: ToolContext, p: LinkInput) -> dict:
        doc = _doc(ctx, p.path)
        kind, tid, title = _describe_target(ctx, p)
        note = " ".join(sanitize_text(p.note or "").split())[:300] or None
        if kind == "project":
            exists = ctx.db.scalars(select(ProjectLink).where(ProjectLink.project_id == int(tid), ProjectLink.kind == "note",
                                                              ProjectLink.target == doc.path)).first()
            if not exists:
                ctx.db.add(ProjectLink(project_id=int(tid), kind="note", target=doc.path, title=note))
        else:
            exists = ctx.db.scalars(select(DocumentLink).where(DocumentLink.path == doc.path, DocumentLink.target_type == kind,
                                                               DocumentLink.target_id == tid)).first()
            if not exists:
                ctx.db.add(DocumentLink(path=doc.path, target_type=kind, target_id=tid, title=title[:200] if title else None, note=note))
        ctx.db.flush()
        return {"linked": {"path": doc.path, "to": kind, "id": tid, "title": title}, **({"note": "Already linked."} if exists else {})}

    def unlink_document(ctx: ToolContext, p: LinkInput) -> dict:
        doc_path = p.path.strip()
        try:
            doc_path = _doc(ctx, p.path).path
        except ToolError:
            pass  # the file may be gone: the link can still be removed by its stored path
        kind, tid, _ = _describe_target(ctx, p) if p.email_message_id is None else ("email", p.email_message_id.strip(), "")
        if kind == "project":
            rows = list(ctx.db.scalars(select(ProjectLink).where(ProjectLink.project_id == int(tid), ProjectLink.target == doc_path)))
        else:
            rows = list(ctx.db.scalars(select(DocumentLink).where(DocumentLink.path == doc_path, DocumentLink.target_type == kind,
                                                                  DocumentLink.target_id == tid)))
        for r in rows:
            ctx.db.delete(r)
        ctx.db.flush()
        if not rows:
            raise ToolError("Those weren't linked.")
        return {"unlinked": {"path": doc_path, "from": kind, "id": tid}}

    def document_links(ctx: ToolContext, p: PathInput) -> dict:
        doc = _doc(ctx, p.path)
        return {"path": doc.path, "links": links_for(ctx.db, doc.path)}

    # ---- move / rename
    def _move_plan(ctx: ToolContext, p: MoveInput):
        doc = _doc(ctx, p.path)
        src, rel_src = _inside(doc.path, must_exist=True)
        dst, rel_dst = _inside(p.new_path, must_exist=False)
        if dst.suffix.lower() != src.suffix.lower():
            raise ToolError("Keep the file's extension (a move can't change its type).")
        if dst.exists():
            raise ToolError(f"There's already a file at {rel_dst}: Keel never overwrites.")
        return doc, src, rel_src, dst, rel_dst

    def move_document(ctx: ToolContext, p: MoveInput) -> dict:
        doc, src, rel_src, dst, rel_dst = _move_plan(ctx, p)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        doc.path = rel_dst  # same content: keep its index rows, links follow
        for link in ctx.db.scalars(select(DocumentLink).where(DocumentLink.path == rel_src)):
            link.path = rel_dst
        for link in ctx.db.scalars(select(ProjectLink).where(ProjectLink.kind == "note", ProjectLink.target == rel_src)):
            link.target = rel_dst
        ctx.db.flush()
        return {"moved": {"from": rel_src, "to": rel_dst}}

    def summarize_move(ctx: ToolContext, p: MoveInput) -> str:
        doc, _, rel_src, _, rel_dst = _move_plan(ctx, p)
        n = len(links_for(ctx.db, rel_src))
        return f"Move {rel_src} → {rel_dst} in your notes folder" + (f" ({n} link(s) follow it)" if n else "") + \
               ". Other apps that point at the old place won't find it there."

    # ---- create / edit notes
    def create_note(ctx: ToolContext, p: CreateNoteInput) -> dict:
        full, rel = _inside(p.path, must_exist=False)
        if full.suffix.lower() not in TEXT_NOTES:
            raise ToolError("New notes are .md or .txt files.")
        if full.exists():
            raise ToolError(f"There's already a file at {rel}: Keel never overwrites. Use edit_note.")
        content = sanitize_text(p.content)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        _reindex(ctx)
        return {"created": rel, "chars": len(content)}

    def _edit_plan(ctx: ToolContext, p: EditNoteInput) -> tuple[Path, str, str, str]:
        doc = _doc(ctx, p.path)
        full, rel = _inside(doc.path, must_exist=True)
        if full.suffix.lower() not in TEXT_NOTES:
            raise ToolError("Keel edits text notes (.md, .txt) only; Word and PDF files are left as they are.")
        if full.stat().st_size > MAX_NOTE_BYTES:
            raise ToolError("That note is too large to edit here.")
        old = full.read_text(encoding="utf-8", errors="replace")
        if p.append is not None:
            new = old.rstrip("\n") + "\n\n" + sanitize_text(p.append).strip() + "\n"
        else:
            count = old.count(p.find)
            if count != 1:
                raise ToolError("That text isn't in the note." if count == 0 else
                                f"That text appears {count} times: include more around it so it matches exactly one place.")
            new = old.replace(p.find, sanitize_text(p.replace), 1)
        return full, rel, old, new

    def edit_note(ctx: ToolContext, p: EditNoteInput) -> dict:
        full, rel, old, new = _edit_plan(ctx, p)
        backup = root / BACKUPS / datetime.now().strftime("%Y%m%d-%H%M%S") / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(old, encoding="utf-8")
        full.write_text(new, encoding="utf-8")
        _reindex(ctx)
        return {"edited": rel, "backup": backup.relative_to(root).as_posix(), "chars_before": len(old), "chars_after": len(new)}

    def summarize_edit(ctx: ToolContext, p: EditNoteInput) -> str:
        _, rel, _, _ = _edit_plan(ctx, p)
        if p.append is not None:
            return f"Add to the end of {rel}:\n{p.append[:1500]}"
        return f"In {rel}, replace:\n{p.find[:800]}\nwith:\n{p.replace[:1500]}\n(The old version is kept in {BACKUPS}/.)"

    registry.register(Tool(name="link_document", permission=Permission.WRITE, input_model=LinkInput, handler=link_document,
                           description="Connect a document in the notes folder to an application, task, project or email "
                                       "(e.g. the transcript for the DAAD application)."))
    registry.register(Tool(name="unlink_document", permission=Permission.WRITE, input_model=LinkInput, handler=unlink_document,
                           description="Remove a document's connection to an application, task, project or email."))
    registry.register(Tool(name="document_links", permission=Permission.READ, input_model=PathInput, handler=document_links,
                           description="What a document is connected to: applications, tasks, projects, emails."))
    registry.register(Tool(name="move_document", permission=Permission.HIGH_IMPACT, input_model=MoveInput, handler=move_document,
                           summarize=summarize_move,
                           description="Move or rename a file inside the notes folder (never overwrites; links follow). The user approves."))
    registry.register(Tool(name="create_note", permission=Permission.WRITE, input_model=CreateNoteInput, handler=create_note,
                           description="Create a new .md or .txt note in the notes folder (never overwrites)."))
    registry.register(Tool(name="edit_note", permission=Permission.WRITE, input_model=EditNoteInput, handler=edit_note,
                           summarize=summarize_edit,
                           description="Edit a .md or .txt note: replace one exact passage, or append. Keeps a backup of the old version."))


def links_for(db, path: str) -> list[dict]:
    out = [{"id": link.id, "to": link.target_type, "target_id": link.target_id, "title": link.title, "note": link.note,
            "linked_on": link.created_at.date().isoformat()} for link in db.scalars(select(DocumentLink).where(DocumentLink.path == path))]
    from app.db.models import Project

    for link in db.scalars(select(ProjectLink).where(ProjectLink.kind == "note", ProjectLink.target == path)):
        proj = db.get(Project, link.project_id)
        out.append({"id": f"p{link.id}", "to": "project", "target_id": str(link.project_id), "title": proj.name if proj else None, "note": link.title})
    return out


def documents_linked_to(db, target_type: str, target_id: str) -> list[dict]:
    rows = db.scalars(select(DocumentLink).where(DocumentLink.target_type == target_type, DocumentLink.target_id == str(target_id)))
    paths = {d.path for d in db.scalars(select(Document))}
    return [{"path": r.path, "note": r.note, **({"missing": True} if r.path not in paths else {})} for r in rows]

