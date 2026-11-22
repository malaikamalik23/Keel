"""File management: links between documents and applications/tasks/projects/emails, moving, creating and editing notes."""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from app.db.models import Document, DocumentLink, Opportunity, PendingAction, Project, ProjectLink, Task
from app.tools.base import ToolContext

TZ = ZoneInfo("Asia/Karachi")


@pytest.fixture
def world(make_container, tmp_path):
    notes = tmp_path / "notes"
    (notes / "uploads").mkdir(parents=True)
    (notes / "uploads" / "transcript-bs.md").write_text("# Transcript\nCGPA 3.4", encoding="utf-8")
    (notes / "journal.md").write_text("# Journal\n\nMet Dr. Khan today.\nTODO: send chapter 2.\n", encoding="utf-8")
    (notes / "brief.txt").write_text("Deadline: 15 December 2026.", encoding="utf-8")
    c = make_container(notes_dir=str(notes))
    c.index_notes()
    with c.session_factory() as db:
        db.add_all([Opportunity(title="DAAD EPOS", documents=[{"name": "Transcript", "status": "needed"}]), Task(title="Send chapter 2"),
                    Project(name="FYP")])
        db.commit()
    return c, notes


def run(c, db, name, args, tainted=False):
    ctx = ToolContext(db=db, tz=TZ)
    ctx.tainted = tainted
    out = c.executor.run(ctx, name, args)
    db.commit()
    return out


def test_link_a_document_to_an_application_task_and_project_and_list_its_links(world):
    c, _ = world
    with c.session_factory() as db:
        assert run(c, db, "link_document", {"path": "transcript-bs.md", "opportunity_id": 1, "note": "final transcript"}).status == "ok"
        assert run(c, db, "link_document", {"path": "journal.md", "task_id": 1}).status == "ok"
        assert run(c, db, "link_document", {"path": "journal.md", "project_id": 1}).status == "ok"
        again = run(c, db, "link_document", {"path": "transcript-bs.md", "opportunity_id": 1})
        assert again.result["note"] == "Already linked." and db.query(DocumentLink).count() == 2
        links = run(c, db, "document_links", {"path": "journal.md"}).result["links"]
        assert {(link["to"], link["title"]) for link in links} == {("task", "Send chapter 2"), ("project", "FYP")}
        assert run(c, db, "link_document", {"path": "journal.md", "task_id": 1, "project_id": 1}).status == "error"  # exactly one target
        assert run(c, db, "link_document", {"path": "journal.md", "opportunity_id": 9}).status == "error"
        assert run(c, db, "unlink_document", {"path": "journal.md", "task_id": 1}).status == "ok"
        assert run(c, db, "unlink_document", {"path": "journal.md", "task_id": 1}).status == "error"


def test_links_feed_application_readiness_and_prepare_application(world):
    c, _ = world
    with c.session_factory() as db:
        run(c, db, "link_document", {"path": "transcript-bs.md", "opportunity_id": 1})
        r = run(c, db, "application_readiness", {"opportunity_id": 1}).result
        assert r["documents"][0]["linked"] == ["uploads/transcript-bs.md"]
        prep = run(c, db, "prepare_application", {"opportunity_id": 1}).result
        assert prep["linked_documents"] == [{"path": "uploads/transcript-bs.md", "note": None}]


def test_moving_needs_approval_never_overwrites_and_links_follow(world):
    c, notes = world
    with c.session_factory() as db:
        run(c, db, "link_document", {"path": "transcript-bs.md", "opportunity_id": 1})
        run(c, db, "link_document", {"path": "transcript-bs.md", "project_id": 1})
        out = run(c, db, "move_document", {"path": "transcript-bs.md", "new_path": "applications/daad/transcript.md"})
        assert out.status == "pending_confirmation" and (notes / "uploads" / "transcript-bs.md").exists()
        summary = db.get(PendingAction, out.pending_action_id).summary
        assert summary.startswith("Move uploads/transcript-bs.md → applications/daad/transcript.md") and "2 link(s) follow it" in summary
        done = c.executor.confirm(ToolContext(db=db, tz=TZ), out.pending_action_id)
        db.commit()
        assert done.status == "ok" and (notes / "applications" / "daad" / "transcript.md").exists()
        assert db.query(Document).filter_by(path="applications/daad/transcript.md").count() == 1
        assert db.query(DocumentLink).one().path == "applications/daad/transcript.md"
        assert db.query(ProjectLink).one().target == "applications/daad/transcript.md"
        c.index_notes()  # a later re-index keeps the moved file and its links
        assert db.query(DocumentLink).count() == 1
        for bad in ({"path": "journal.md", "new_path": "../outside.md"},          # outside the notes folder
                    {"path": "journal.md", "new_path": ".hidden/j.md"},           # hidden folder
                    {"path": "journal.md", "new_path": "journal.txt"},            # changes the file type
                    {"path": "journal.md", "new_path": "applications/daad/transcript.md"}):  # a file is already there
            assert run(c, db, "move_document", bad).status == "error", bad


def test_create_and_edit_notes_with_backups(world):
    c, notes = world
    with c.session_factory() as db:
        assert run(c, db, "create_note", {"path": "applications/daad/checklist.md", "content": "# DAAD\n- transcript"}).status == "ok"
        assert db.query(Document).filter_by(path="applications/daad/checklist.md").count() == 1  # indexed straight away
        assert run(c, db, "create_note", {"path": "journal.md", "content": "x"}).status == "error"  # never overwrites
        assert run(c, db, "create_note", {"path": "x.pdf", "content": "x"}).status == "error"

        out = run(c, db, "edit_note", {"path": "journal.md", "find": "TODO: send chapter 2.", "replace": "DONE: sent chapter 2."})
        assert out.status == "ok"
        assert "DONE: sent chapter 2." in (notes / "journal.md").read_text(encoding="utf-8")
        backup = notes / out.result["backup"]
        assert backup.read_text(encoding="utf-8").count("TODO: send chapter 2.") == 1 and out.result["backup"].startswith(".keel-backups/")
        assert db.query(Document).filter(Document.path.like(".keel-backups%")).count() == 0  # backups aren't indexed
        assert run(c, db, "edit_note", {"path": "journal.md", "append": "Met again on Friday."}).status == "ok"
        assert (notes / "journal.md").read_text(encoding="utf-8").rstrip().endswith("Met again on Friday.")
        assert run(c, db, "edit_note", {"path": "journal.md", "find": "nowhere", "replace": "x"}).result["error"] == "That text isn't in the note."
        (notes / "twice.md").write_text("a\na\n", encoding="utf-8")
        c.index_notes()
        assert "appears 2 times" in run(c, db, "edit_note", {"path": "twice.md", "find": "a", "replace": "b"}).result["error"]


def test_editing_after_reading_untrusted_text_needs_approval_with_the_exact_change(world):
    c, notes = world
    with c.session_factory() as db:
        out = run(c, db, "edit_note", {"path": "journal.md", "find": "Met Dr. Khan today.", "replace": "Met nobody."}, tainted=True)
        assert out.status == "pending_confirmation"
        summary = db.get(PendingAction, out.pending_action_id).summary
        assert "In journal.md, replace:\nMet Dr. Khan today.\nwith:\nMet nobody." in summary
        assert "Met Dr. Khan today." in (notes / "journal.md").read_text(encoding="utf-8")  # unchanged until approved


def test_workflows_cannot_move_or_edit(world):
    from app.services.workflows import validate_definition

    c, _ = world
    for step in ({"tool": "move_document", "args": {"path": "a.md", "new_path": "b.md"}, "approve": True},
                 {"tool": "edit_note", "args": {"path": "a.md", "append": "x"}}):
        with pytest.raises(ValueError, match="not a tool workflows can use"):
            validate_definition(c.registry, {"type": "manual"}, [step])


def test_dashboard_link_endpoints(tmp_path):
    from app.api.main import create_app
    from tests.conftest import make_settings
    from tests.fakes import ScriptedLLM, local_client

    notes = tmp_path / "n"
    notes.mkdir()
    (notes / "cv.md").write_text("# CV", encoding="utf-8")
    app = create_app(make_settings(notes_dir=str(notes)), ScriptedLLM([]))
    c = app.state.container
    c.index_notes()
    with c.session_factory() as db:
        db.add_all([Opportunity(title="DAAD EPOS"), Project(name="FYP")])
        db.commit()
        run(c, db, "link_document", {"path": "cv.md", "opportunity_id": 1})
        run(c, db, "link_document", {"path": "cv.md", "project_id": 1})
    client = local_client(app)
    links = client.get("/api/document-links").json()
    assert [(link["to"], link["title"]) for link in links["cv.md"]] == [("opportunity", "DAAD EPOS"), ("project", "FYP")]
    assert client.delete(f"/api/document-links/{links['cv.md'][1]['id']}").status_code == 204
    assert client.delete("/api/document-links/1").status_code == 204
    assert client.get("/api/document-links").json() == {}
    assert client.delete("/api/document-links/nope").status_code == 404
    assert (notes / "cv.md").exists()  # unlinking never touches the file
