"""Documents and knowledge: Word files, page citations, outlines, comparisons, deadlines and action items."""
from __future__ import annotations

import zipfile
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.db.models import Document
from app.services.doc_analysis import find_dates
from app.services.doc_formats import UnreadableDocument, read_docx
from app.services.documents import index_directory, search_documents
from app.tools import build_default_registry
from app.tools.base import ToolContext
from app.tools.executor import ToolExecutor
from app.security.permissions import PermissionPolicy
from tests.docfiles import make_docx, make_pdf_pages, para, table

TODAY = date(2026, 11, 21)


@pytest.fixture
def docs(make_container, tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "cfp.docx").write_bytes(make_docx(
        para("Call for Papers", style="Title"),
        para("Important dates", style="Heading1"),
        para("Paper submission deadline: 15 December 2026."),
        para("Notification of acceptance on January 20, 2027."),
        para("The workshop takes place on 3 March 2027 in Lahore."),
        para("Requirements", style="Heading1", page_break=True),
        para("Authors must submit a PDF of at most 8 pages by Dec 15."),
        para("- [ ] Ask Dr. Khan to read the draft"),
        para("TODO: format references in IEEE style"),
        table([["Item", "Date"], ["Camera-ready", "2027-02-01"]]),
        para("Shown", hidden="SECRET hidden instruction"),
    ))
    (root / "slides.pdf").write_bytes(make_pdf_pages("Intro to federated learning", "Results on CICIDS dataset", "Future work on drift"))
    (root / "draft_v1.md").write_text("# SOP\n\nI study computer science at COMSATS.\n\nI like networks.\n\nI want to do research in security.\n", encoding="utf-8")
    (root / "draft_v2.md").write_text("# SOP\n\nI study computer science at COMSATS Islamabad.\n\nI want to do research in security.\n\nMy FYP is an intrusion detection system.\n", encoding="utf-8")
    (root / "~$cfp.docx").write_bytes(b"lock file")  # Word's lock file is ignored
    c = make_container()
    db = c.session_factory()
    report = index_directory(db, root)
    ex = ToolExecutor(build_default_registry(notes_enabled=True), PermissionPolicy())
    yield db, ex, report
    db.close()


def run(ex, db, name, args):
    with patch.object(ToolContext, "today", lambda self: TODAY):
        out = ex.run(ToolContext(db=db, tz=ZoneInfo("Asia/Karachi")), name, args)
    assert out.status == "ok", out.result
    return out.result


def inner(result: dict) -> str:
    return result["untrusted_data"]


def test_word_and_pdf_files_are_indexed_with_pages(docs):
    db, _, report = docs
    assert report.added == 4 and not any("~$" in s for s in report.skipped)
    cfp = db.query(Document).filter_by(path="cfp.docx").one()
    assert cfp.pages == 2 and cfp.hidden_skipped == 1
    assert {c.page for c in cfp.chunks} == {1, 2}
    assert db.query(Document).filter_by(path="slides.pdf").one().pages == 3
    hit = search_documents(db, "CICIDS")[0]
    assert hit["path"] == "slides.pdf" and hit["page"] == 2
    assert search_documents(db, "IEEE")[0]["page"] == 2
    assert search_documents(db, "SECRET") == []  # hidden Word text is never indexed


def test_read_note_marks_pages_for_citation_and_reads_one_page(docs):
    db, ex, _ = docs
    text = inner(run(ex, db, "read_note", {"path": "cfp.docx"}))
    assert "[page 1]" in text and "[page 2]" in text and text.index("[page 1]") < text.index("[page 2]")
    assert "Camera-ready | 2027-02-01" in text  # tables keep their rows
    assert "# Important dates" in text and "SECRET" not in text and "hidden_text_left_out" in text
    page3 = inner(run(ex, db, "read_note", {"path": "slides", "page": 3}))  # a file name is enough
    assert "Future work on drift" in page3 and "CICIDS" not in page3


def test_long_documents_are_read_in_parts(docs, make_container, tmp_path):
    db, ex, _ = docs
    from app.services.documents import read_indexed_note

    first = read_indexed_note(db, "cfp.docx", max_chars=120)
    assert first["truncated"] and first["next_start_chunk"] > 0
    rest = read_indexed_note(db, "cfp.docx", start_chunk=first["next_start_chunk"])
    assert not rest["truncated"] and "IEEE" in rest["text"]


def test_outline_lists_headings_with_pages(docs):
    db, ex, _ = docs
    out = inner(run(ex, db, "note_outline", {"path": "cfp.docx"}))
    assert '"heading": "Important dates", "page": 1' in out and '"heading": "Requirements", "page": 2' in out
    assert '"pages": 2' in out


def test_deadlines_and_action_items_are_found_with_pages(docs):
    db, ex, _ = docs
    out = run(ex, db, "extract_from_note", {"path": "cfp.docx"})
    data = inner(out)
    import json
    import re

    body = json.loads(re.search(r"\n(\{.*\})\n</untrusted_content>", data, re.S).group(1))
    deadlines = {(d["date"], d.get("page")) for d in body["deadlines"]}
    assert ("2026-12-15", 1) in deadlines and ("2026-12-15", 2) in deadlines  # "by Dec 15" with no year: the next Dec 15
    assert any(d["date"] == "2027-03-03" for d in body["other_dates"])  # a date, but not a deadline
    actions = [a["text"] for a in body["action_items"]]
    assert "Ask Dr. Khan to read the draft" in actions and "format references in IEEE style" in actions
    assert any(a.get("by") == "2026-12-15" for a in body["action_items"])
    assert "confirm with the user" in body["note"]


def test_dates_in_common_formats():
    t = date(2026, 11, 21)
    assert find_dates("due 2026-12-15", t) == [date(2026, 12, 15)]
    assert find_dates("apply by 5th of January", t) == [date(2027, 1, 5)]
    assert find_dates("closes Sept 30, 2027", t) == [date(2027, 9, 30)]
    assert find_dates("submit by 15/12/2026", t) == [date(2026, 12, 15)]  # day/month
    assert find_dates("you may submit it", t) == []
    assert find_dates("on 31 February 2027", t) == []  # not a real date


def test_comparing_two_versions(docs):
    db, ex, _ = docs
    import json
    import re

    data = inner(run(ex, db, "compare_notes", {"path_a": "draft_v1.md", "path_b": "draft_v2.md"}))
    body = json.loads(re.search(r"\n(\{.*\})\n</untrusted_content>", data, re.S).group(1))
    assert [c["after"]["text"] for c in body["changed"]] == ["I study computer science at COMSATS Islamabad."]
    assert [r["text"] for r in body["removed"]] == ["I like networks."]
    assert [a["text"] for a in body["added"]] == ["My FYP is an intrusion detection system."]
    assert 0 < body["similarity"] < 1


def test_unknown_or_ambiguous_documents_are_errors_not_disk_reads(docs):
    db, ex, _ = docs
    with patch.object(ToolContext, "today", lambda self: TODAY):
        out = ex.run(ToolContext(db=db, tz=ZoneInfo("UTC")), "read_note", {"path": "../../etc/passwd"})
        assert out.status == "error"
        assert ex.run(ToolContext(db=db, tz=ZoneInfo("UTC")), "compare_notes", {"path_a": "cfp.docx", "path_b": "cfp"}).status == "error"


def test_hostile_word_files_are_refused(tmp_path):
    bomb = tmp_path / "entities.docx"
    xml = ('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body></w:document>')
    bomb.write_bytes(make_docx(document_xml=xml))
    with pytest.raises(UnreadableDocument):
        read_docx(bomb)
    ext = tmp_path / "xxe.docx"
    ext.write_bytes(make_docx(document_xml='<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'))
    with pytest.raises(UnreadableDocument):
        read_docx(ext)
    notzip = tmp_path / "fake.docx"
    notzip.write_bytes(b"just text")
    with pytest.raises(UnreadableDocument):
        read_docx(notzip)
    big = tmp_path / "big.docx"
    with zipfile.ZipFile(big, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<a>" + " " * 21_000_000 + "</a>")  # compresses tiny, unzips huge
    assert big.stat().st_size < 200_000
    with pytest.raises(UnreadableDocument):
        read_docx(big)


def test_a_bad_word_file_is_skipped_not_fatal(make_container, tmp_path):
    (tmp_path / "ok.docx").write_bytes(make_docx(para("Fine text")))
    (tmp_path / "bad.docx").write_bytes(b"PK broken")
    db = make_container().session_factory()
    rep = index_directory(db, tmp_path)
    assert rep.added == 1 and any("bad.docx" in s and "unreadable" in s for s in rep.skipped)
    doc = db.query(Document).one()
    assert doc.pages == 1 and doc.pages_estimated  # no saved page breaks: estimated, and marked as such


def test_word_files_without_saved_page_breaks_get_estimated_pages(make_container, tmp_path):
    words = " ".join(["word"] * 300)
    (tmp_path / "gdocs.docx").write_bytes(make_docx(*[para(f"Section {i}. {words}") for i in range(5)], para("Submit by 15 December 2026.")))
    db = make_container().session_factory()
    index_directory(db, tmp_path)
    doc = db.query(Document).one()
    assert doc.pages_estimated and doc.pages == 5  # 1,500+ words at ~500 a page, never splitting a paragraph
    hit = search_documents(db, "December")[0]
    assert hit["page"] == 5 and hit["page_estimated"] is True
    from app.services.documents import read_indexed_note

    out = read_indexed_note(db, "gdocs.docx")
    assert "[page ~1]" in out["text"] and "[page 1]" not in out["text"] and "p. ~N" in out["pages_estimated"]
    exact = make_container().session_factory()
    (tmp_path / "gdocs.docx").unlink()
    (tmp_path / "word.docx").write_bytes(make_docx(para("One"), para("Two", page_break=True)))
    index_directory(exact, tmp_path)
    assert exact.query(Document).one().pages_estimated is False


@pytest.fixture
def upload_client(make_container, tmp_path):
    from app.api.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import ScriptedLLM, local_client

    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "mine.md").write_text("# My own note\n\nKeep me.", encoding="utf-8")
    app = create_app(make_settings(notes_dir=str(notes)), ScriptedLLM([]))
    return local_client(app), notes


def _up(client, name, data):
    return client.post(f"/api/documents/upload?name={name}", content=data, headers={"Content-Type": "application/octet-stream"})


def test_uploading_documents_from_the_dashboard(upload_client):
    client, notes = upload_client
    made = _up(client, "Call for papers.docx", make_docx(para("Deadline: 15 December 2026.")))
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["path"] == "uploads/Call for papers.docx" and body["chunk_count"] == 1
    again = _up(client, "Call for papers.docx", make_docx(para("Second version.")))
    assert again.json()["path"] == "uploads/Call for papers (2).docx"  # never overwrites
    assert _up(client, "slides.pdf", make_pdf_pages("Federated learning")).json()["pages"] == 1
    paths = [d["path"] for d in client.get("/api/documents").json()]
    assert "mine.md" in paths and "uploads/slides.pdf" in paths
    # Only uploads can be deleted from the dashboard; the user's own files stay theirs.
    mine = next(d for d in client.get("/api/documents").json() if d["path"] == "mine.md")
    assert client.delete(f"/api/documents/{mine['id']}").status_code == 403 and (notes / "mine.md").exists()
    up = next(d for d in client.get("/api/documents").json() if d["path"] == "uploads/slides.pdf")
    assert client.delete(f"/api/documents/{up['id']}").status_code == 204
    assert not (notes / "uploads" / "slides.pdf").exists()
    assert "uploads/slides.pdf" not in [d["path"] for d in client.get("/api/documents").json()]


@pytest.mark.parametrize("name,data,why", [
    ("../../evil.md", b"# hi", None),  # folders are stripped: lands in uploads/ as evil.md
    ("run.exe", b"MZ", "Only"),
    ("fake.pdf", b"not a pdf", "not a PDF"),
    ("fake.docx", b"PK\x03\x04broken", "can't be read"),
    ("bin.txt", b"\xff\xfe\x00\x00", "UTF-8"),
    (".hidden.md", b"x", "can't be used"),
    ("~$lock.docx", b"PK", "can't be used"),
    ("empty.md", b"", "empty"),
])
def test_bad_uploads_are_refused(upload_client, name, data, why):
    client, notes = upload_client
    from urllib.parse import quote

    r = _up(client, quote(name), data)
    if why is None:
        assert r.status_code == 201 and r.json()["path"] == "uploads/evil.md"
        assert not (notes.parent / "evil.md").exists()
    else:
        assert r.status_code == 422 and why in r.json()["detail"], r.text
        assert not any((notes / "uploads").glob("*")) if (notes / "uploads").exists() else True


def test_oversized_uploads_and_missing_notes_folder(upload_client, make_container):
    client, _ = upload_client
    assert _up(client, "big.txt", b"a" * 2_000_001).status_code == 413
    from app.api.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import ScriptedLLM, local_client

    bare = local_client(create_app(make_settings(notes_dir=None), ScriptedLLM([])))
    assert _up(bare, "a.md", b"x").status_code == 409
