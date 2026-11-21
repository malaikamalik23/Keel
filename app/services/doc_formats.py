"""Turn a file into text pages: plain text, PDF (one entry per page) and Word .docx.

A .docx is a zip of XML. It is read with the standard library only, defensively, because a document can come from
anyone (a downloaded call for papers, a file a classmate sent):
- the XML must be small once unzipped (zip bombs) and may not declare a DOCTYPE (entity-expansion attacks);
- text Word marks as hidden (w:vanish) is left out and counted, since hidden text is a known way to slip
  instructions past a human reader;
- page numbers come from the page breaks Word saved in the file, so they match what the author saw. Files saved
  without them (Google Docs, LibreOffice, some exports) get pages estimated from the word count, and are marked as
  estimated so citations say "p. ~3", never a page number presented as exact.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET

TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".tex", ".org"}
PDF_EXT = ".pdf"
DOCX_EXT = ".docx"
MAX_PDF_PAGES = 300
MAX_DOCX_XML = 20_000_000
WORDS_PER_PAGE = 500  # a typical A4/Letter page of 11-12pt single-spaced text

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UnreadableDocument(Exception):
    pass


@dataclass
class Pages:
    pages: list[tuple[int | None, str]] = field(default_factory=list)  # (page number or None, text)
    page_count: int | None = None
    hidden_skipped: int = 0
    estimated: bool = False  # page numbers come from a word count, not from the file


def read_pages(path: Path) -> Pages:
    ext = path.suffix.lower()
    if ext == PDF_EXT:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages[:MAX_PDF_PAGES])]
        return Pages(pages, len(reader.pages))
    if ext == DOCX_EXT:
        return read_docx(path)
    return Pages([(None, path.read_bytes().decode("utf-8", errors="replace"))])


def _run_hidden(run: Any) -> bool:
    props = run.find(f"{W}rPr")
    if props is None:
        return False
    vanish = props.find(f"{W}vanish")
    return vanish is not None and vanish.get(f"{W}val", "true") not in ("0", "false")


def read_docx(path: Path) -> Pages:
    try:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML:
                raise UnreadableDocument("document too large once unzipped")
            data = z.read(info)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise UnreadableDocument("not a Word document") from exc
    if b"<!DOCTYPE" in data[:4096].upper() or b"<!ENTITY" in data.upper():
        raise UnreadableDocument("document declares a DOCTYPE")
    try:
        root = SafeET.fromstring(data)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise UnreadableDocument("broken XML") from exc
    body = root.find(f"{W}body")
    if body is None:
        return Pages()

    out = Pages()
    page, saw_break = 1, False
    current: list[str] = []

    def flush():
        if current:
            out.pages.append((page, "\n\n".join(current)))
            current.clear()

    def paragraph(p: Any) -> str:
        nonlocal page, saw_break
        style = p.find(f"{W}pPr/{W}pStyle")
        parts: list[str] = []
        for run in p.iter(f"{W}r"):
            for el in run:
                tag = el.tag
                if tag in (f"{W}lastRenderedPageBreak",) or (tag == f"{W}br" and el.get(f"{W}type") == "page"):
                    if parts or current:  # a break at the very start of the document doesn't start page 2
                        text = "".join(parts).strip()
                        if text:
                            current.append(text)
                        parts.clear()
                        flush()
                        page += 1
                        saw_break = True
                    continue
                if _run_hidden(run):
                    if tag == f"{W}t" and (el.text or "").strip():
                        out.hidden_skipped += 1
                    continue
                if tag == f"{W}t":
                    parts.append(el.text or "")
                elif tag == f"{W}tab":
                    parts.append("\t")
                elif tag in (f"{W}br", f"{W}cr"):
                    parts.append("\n")
        text = "".join(parts).strip()
        if text and style is not None and (style.get(f"{W}val") or "").lower().startswith(("heading", "title")):
            level = (style.get(f"{W}val") or "")[-1:]
            text = "#" * (int(level) if level.isdigit() else 1) + " " + text
        return text

    for block in body:
        if block.tag == f"{W}p":
            text = paragraph(block)
            if text:
                current.append(text)
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = [" ".join(t for t in (paragraph(p) for p in cell.iter(f"{W}p")) if t) for cell in row.iter(f"{W}tc")]
                if any(cells):
                    current.append(" | ".join(cells))
    flush()
    if saw_break:
        out.page_count = page
        return out
    return estimate_pages(out)


def estimate_pages(out: Pages, words_per_page: int = WORDS_PER_PAGE) -> Pages:
    """No page breaks saved in the file: split by word count and say the numbers are estimates."""
    paragraphs = [p for _, text in out.pages for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return out
    pages: list[tuple[int | None, str]] = []
    page, words, current = 1, 0, []
    for p in paragraphs:
        n = len(p.split())
        if current and words + n > words_per_page:
            pages.append((page, "\n\n".join(current)))
            page, words, current = page + 1, 0, []
        current.append(p)
        words += n
    pages.append((page, "\n\n".join(current)))
    out.pages, out.page_count, out.estimated = pages, page, True
    return out
