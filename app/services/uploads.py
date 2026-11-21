"""Save a document the user uploaded from the dashboard into NOTES_DIR/uploads, then index it.

The file lands in the notes folder like any other note, so the notes tools treat it the same way (fenced as
untrusted). Checks, all before anything is kept:
- the name is reduced to a plain file name (no folders, no '..', no hidden or Word lock files) with an allowed
  extension, and never overwrites an existing file;
- the content must match the extension (a PDF starts with %PDF-, a .docx is a readable Word file, text is UTF-8)
  and fit the indexer's size limit;
- only files inside uploads/ can be deleted again from the dashboard.
"""
from __future__ import annotations

import re
import tempfile
import unicodedata
from pathlib import Path

from app.services.doc_formats import DOCX_EXT, PDF_EXT, TEXT_EXT, UnreadableDocument, read_pages
from app.services.documents import MAX_FILE_BYTES

UPLOAD_DIR = "uploads"
ALLOWED = TEXT_EXT | {PDF_EXT, DOCX_EXT}


class UploadRejected(Exception):
    pass


def clean_name(name: str) -> str:
    name = unicodedata.normalize("NFC", name).replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")  # no control/invisible characters
    if name.startswith(("~$", ".")):  # Word lock files and hidden files
        raise UploadRejected("That file name can't be used.")
    name = re.sub(r"[^\w .()+-]", "_", name).strip(" .")
    if not name:
        raise UploadRejected("That file name can't be used.")
    stem, dot, ext = name.rpartition(".")
    if not dot or f".{ext.lower()}" not in ALLOWED:
        raise UploadRejected(f"Only {', '.join(sorted(ALLOWED))} files can be uploaded.")
    return f"{stem[:100]}.{ext.lower()}"


def check_content(name: str, data: bytes) -> None:
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise UploadRejected(f"The file is larger than {MAX_FILE_BYTES // 1_000_000} MB.")
    ext = Path(name).suffix
    if ext == PDF_EXT and not data.startswith(b"%PDF-"):
        raise UploadRejected("That is not a PDF.")
    if ext in TEXT_EXT:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadRejected("Text files must be UTF-8.") from None
        if "\x00" in text:
            raise UploadRejected("That is not a text file.")
    if ext == DOCX_EXT:
        if not data.startswith(b"PK\x03\x04"):
            raise UploadRejected("That is not a Word (.docx) file.")
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.docx"
            probe.write_bytes(data)
            try:
                read_pages(probe)
            except UnreadableDocument as exc:
                raise UploadRejected(f"The Word file can't be read ({exc}).") from None


def save_upload(notes_dir: Path, name: str, data: bytes) -> str:
    """Store the file; returns its path relative to the notes folder (what the notes tools use)."""
    name = clean_name(name)
    check_content(name, data)
    root = notes_dir.expanduser().resolve()
    folder = root / UPLOAD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.resolve().is_relative_to(root):
        raise UploadRejected("The uploads folder is not inside the notes folder.")
    stem, ext = name.rsplit(".", 1)
    target, n = folder / name, 2
    while target.exists():
        target, n = folder / f"{stem} ({n}).{ext}", n + 1
    with open(target, "xb") as f:  # 'x': never overwrite, even in a race
        f.write(data)
    return target.relative_to(root).as_posix()


def delete_upload(notes_dir: Path, rel_path: str) -> None:
    root = notes_dir.expanduser().resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root / UPLOAD_DIR) or target.parent != (root / UPLOAD_DIR).resolve():
        raise UploadRejected("Only files uploaded from the dashboard can be deleted here.")
    target.unlink(missing_ok=True)
