"""Notes search: index a folder of text files into SQLite FTS5 and query it.

Security properties (tested):
- only files physically inside the notes root are read; symlinks that point outside it are skipped;
- `read_note` never touches the filesystem -- it serves text from the index, and only for paths that were indexed,
  so there is no path to traverse;
- hidden folders (.git, .obsidian ...), node_modules and virtualenvs are skipped, files over 2 MB are skipped.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, utcnow
from app.services.doc_formats import DOCX_EXT, PDF_EXT, TEXT_EXT, UnreadableDocument, read_pages

SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "site-packages"}
MAX_FILE_BYTES = 2_000_000
CHUNK_CHARS = 900


class NotesUnavailable(Exception):
    pass


@dataclass
class IndexReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"added": self.added, "updated": self.updated, "removed": self.removed, "unchanged": self.unchanged, "skipped": self.skipped[:20]}


def _require_fts(db: Session) -> None:
    if db.get_bind().dialect.name != "sqlite":
        raise NotesUnavailable("Notes search needs SQLite (FTS5).")


def iter_files(root: Path):
    """Yield (path, relative_posix_path) for indexable files that really live under `root`."""
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            ext = p.suffix.lower()
            if ext not in TEXT_EXT and ext not in (PDF_EXT, DOCX_EXT) or name.startswith("~$"):  # ~$x.docx: Word's lock file
                continue
            real = p.resolve()
            if not real.is_relative_to(root):  # symlink pointing outside the notes folder
                continue
            yield real, real.relative_to(root).as_posix()


def read_file_text(path: Path) -> str:
    return "\n\n".join(text for _, text in read_pages(path).pages)


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        while len(para) > limit:  # one huge paragraph: hard-split on whitespace near the limit
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if current and len(current) + len(para) + 2 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return [c for c in chunks if c]


def _delete_doc(db: Session, doc: Document) -> None:
    ids = [c.id for c in doc.chunks]
    for cid in ids:
        db.execute(sql("DELETE FROM chunk_fts WHERE rowid = :id"), {"id": cid})
    db.delete(doc)


def index_directory(db: Session, root: Path) -> IndexReport:
    _require_fts(db)
    if not root.is_dir():
        raise NotesUnavailable(f"Notes folder does not exist: {root}")
    report = IndexReport()
    existing = {d.path: d for d in db.scalars(select(Document))}
    seen: set[str] = set()

    for real, rel in iter_files(root):
        seen.add(rel)
        stat = real.stat()
        if stat.st_size > MAX_FILE_BYTES:
            report.skipped.append(f"{rel} (too large)")
            continue
        doc = existing.get(rel)
        if doc and doc.size == stat.st_size and doc.mtime == stat.st_mtime:
            report.unchanged += 1
            continue
        digest = hashlib.sha256(real.read_bytes()).hexdigest()
        if doc and doc.sha256 == digest:  # touched but identical
            doc.mtime, doc.size = stat.st_mtime, stat.st_size
            report.unchanged += 1
            continue
        try:
            read = read_pages(real)
            chunks = [(page, t) for page, text in read.pages for t in chunk_text(text)]
        except ImportError:
            report.skipped.append(f"{rel} (install pypdf for PDFs)")
            continue
        except UnreadableDocument as exc:
            report.skipped.append(f"{rel} (unreadable: {exc})")
            continue
        except Exception:  # noqa: BLE001 - a corrupt file must not abort indexing
            report.skipped.append(f"{rel} (unreadable)")
            continue
        if doc:
            _delete_doc(db, doc)
            db.flush()
            report.updated += 1
        else:
            report.added += 1
        new = Document(path=rel, sha256=digest, size=stat.st_size, mtime=stat.st_mtime, chunk_count=len(chunks), indexed_at=utcnow(),
                       pages=read.page_count, hidden_skipped=read.hidden_skipped, pages_estimated=read.estimated)
        new.chunks = [Chunk(position=i, text=t, page=page) for i, (page, t) in enumerate(chunks)]
        db.add(new)
        db.flush()
        for c in new.chunks:
            db.execute(sql("INSERT INTO chunk_fts(rowid, text) VALUES (:id, :t)"), {"id": c.id, "t": c.text})

    for rel, doc in existing.items():
        if rel not in seen:
            _delete_doc(db, doc)
            report.removed += 1
    db.commit()
    return report


def _fts_query(query: str, joiner: str) -> str | None:
    tokens = re.findall(r"\w+", query, re.UNICODE)[:12]  # \w is unicode-aware: Urdu works
    return f" {joiner} ".join(f'"{t}"' for t in tokens) if tokens else None


def search_documents(db: Session, query: str, limit: int = 5) -> list[dict]:
    _require_fts(db)
    for joiner in ("AND", "OR"):  # precise first, then relax
        q = _fts_query(query, joiner)
        if q is None:
            return []
        rows = db.execute(
            sql(
                "SELECT d.path AS path, c.position AS position, c.page AS page, d.pages_estimated AS estimated, "
                "snippet(chunk_fts, 0, '[', ']', '…', 28) AS snip, bm25(chunk_fts) AS score "
                "FROM chunk_fts JOIN chunks c ON c.id = chunk_fts.rowid JOIN documents d ON d.id = c.document_id "
                "WHERE chunk_fts MATCH :q ORDER BY score LIMIT :n"
            ),
            {"q": q, "n": limit * 4},
        ).all()
        if rows:
            best: dict[str, dict] = {}
            for r in rows:  # one result per file: its best-scoring chunk
                hit = {"path": r.path, "chunk": r.position, "snippet": r.snip}
                if r.page is not None:
                    hit["page"] = r.page
                    if r.estimated:
                        hit["page_estimated"] = True
                best.setdefault(r.path, hit)
            return list(best.values())[:limit]
    return []


def get_document(db: Session, path: str) -> Document | None:
    return db.scalars(select(Document).where(Document.path == path)).first()


def with_page_marks(chunks: list[Chunk], estimated: bool = False) -> str:
    """Join chunks, marking where each page starts ([page 3], or [page ~3] when estimated) so answers can cite pages."""
    out, last = [], object()
    for c in chunks:
        if c.page is not None and c.page != last:
            out.append(f"[page {'~' if estimated else ''}{c.page}]")
        last = c.page
        out.append(c.text)
    return "\n\n".join(out)


def read_indexed_note(db: Session, path: str, max_chars: int = 8000, page: int | None = None, start_chunk: int = 0) -> dict:
    doc = get_document(db, path)
    if doc is None:
        return {}
    chunks = [c for c in doc.chunks if c.position >= start_chunk and (page is None or c.page == page)]
    taken, size = [], 0
    for c in chunks:
        if taken and size + len(c.text) > max_chars:
            break
        taken.append(c)
        size += len(c.text) + 2
    out = {"path": doc.path, "text": with_page_marks(taken, doc.pages_estimated)[:max_chars], "truncated": len(taken) < len(chunks)}
    if doc.pages:
        out["pages"] = doc.pages
        if doc.pages_estimated:
            out["pages_estimated"] = "No page breaks saved in this Word file: pages are estimated from the word count. Cite as p. ~N."
    if out["truncated"]:
        out["next_start_chunk"] = chunks[len(taken)].position
    if doc.hidden_skipped:
        out["hidden_text_left_out"] = doc.hidden_skipped
    return out
