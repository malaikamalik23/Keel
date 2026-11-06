from zoneinfo import ZoneInfo

import pytest

from app.db.models import Commitment, Memory
from app.tools.base import ToolContext


@pytest.fixture
def env(make_container):
    c = make_container()
    db = c.session_factory()
    ctx = ToolContext(db=db, tz=ZoneInfo("UTC"))
    yield c, ctx, db
    db.close()


# ---------- goals ----------
def test_create_goal_add_milestones_and_decompose_into_tasks(env):
    c, ctx, db = env
    goal_id = c.executor.run(ctx, "create_goal", {"title": "Web penetration testing", "category": "skills"}).result["goal"]["id"]
    out = c.executor.run(
        ctx, "add_milestones",
        {"goal_id": goal_id, "titles": ["HTTP fundamentals", "Authentication", "SQL injection"]},
    )
    assert [m["title"] for m in out.result["created"]] == ["HTTP fundamentals", "Authentication", "SQL injection"]
    milestone_id = out.result["created"][0]["id"]

    tasks_out = c.executor.run(
        ctx, "milestone_to_tasks",
        {"milestone_id": milestone_id, "titles": ["Read HTTP spec basics", "Set up Burp proxy"], "due_date": "2099-01-01"},
    )
    assert len(tasks_out.result["created_tasks"]) == 2

    goal = c.executor.run(ctx, "list_goals", {}).result["goals"][0]
    assert [m["title"] for m in goal["milestones"]] == ["HTTP fundamentals", "Authentication", "SQL injection"]


def test_update_goal_requires_at_least_one_field(env):
    c, ctx, _ = env
    goal_id = c.executor.run(ctx, "create_goal", {"title": "GRE prep"}).result["goal"]["id"]
    out = c.executor.run(ctx, "update_goal", {"goal_id": goal_id})
    assert out.status == "error"


def test_list_goals_filters_by_status(env):
    c, ctx, _ = env
    gid = c.executor.run(ctx, "create_goal", {"title": "Old goal"}).result["goal"]["id"]
    c.executor.run(ctx, "update_goal", {"goal_id": gid, "status": "achieved"})
    assert c.executor.run(ctx, "list_goals", {"status": "active"}).result["total"] == 0
    assert c.executor.run(ctx, "list_goals", {"status": "achieved"}).result["total"] == 1


# ---------- projects ----------
def test_create_project_linked_to_goal(env):
    c, ctx, _ = env
    gid = c.executor.run(ctx, "create_goal", {"title": "Master's applications"}).result["goal"]["id"]
    out = c.executor.run(ctx, "create_project", {"name": "SOP draft", "goal_id": gid, "deadline": "2099-01-01"})
    assert out.result["project"]["goal_id"] == gid

    listed = c.executor.run(ctx, "list_projects", {}).result["projects"]
    assert listed[0]["name"] == "SOP draft"


def test_update_project_status(env):
    c, ctx, _ = env
    pid = c.executor.run(ctx, "create_project", {"name": "FYP"}).result["project"]["id"]
    c.executor.run(ctx, "update_project", {"project_id": pid, "status": "completed"})
    assert c.executor.run(ctx, "list_projects", {"status": "active"}).result["total"] == 0
    assert c.executor.run(ctx, "list_projects", {"status": "completed"}).result["total"] == 1


# ---------- commitments ----------
def test_propose_commitment_defaults_to_proposed_until_confirmed(env):
    c, ctx, db = env
    out = c.executor.run(ctx, "propose_commitment", {"text": "Finish GRE quant set", "deadline": "2099-01-05"})
    cid = out.result["commitment"]["id"]
    assert db.get(Commitment, cid).status == "proposed"

    c.executor.run(ctx, "confirm_commitment", {"commitment_id": cid})
    assert db.get(Commitment, cid).status == "open"


def test_propose_commitment_confirmed_true_skips_the_round_trip(env):
    c, ctx, db = env
    out = c.executor.run(ctx, "propose_commitment", {"text": "Study tonight", "confirmed": True})
    assert db.get(Commitment, out.result["commitment"]["id"]).status == "open"


def test_confirm_commitment_rejects_wrong_status(env):
    c, ctx, _ = env
    cid = c.executor.run(ctx, "propose_commitment", {"text": "x", "confirmed": True}).result["commitment"]["id"]
    out = c.executor.run(ctx, "confirm_commitment", {"commitment_id": cid})
    assert out.status == "error"


def test_pushing_commitment_deadline_later_counts_as_postponement(env):
    c, ctx, db = env
    cid = c.executor.run(ctx, "propose_commitment", {"text": "GRE", "deadline": "2099-01-01", "confirmed": True}).result["commitment"]["id"]
    c.executor.run(ctx, "update_commitment", {"commitment_id": cid, "deadline": "2099-01-05"})
    c.executor.run(ctx, "update_commitment", {"commitment_id": cid, "deadline": "2099-01-03"})  # earlier: not a postponement
    assert db.get(Commitment, cid).postponement_count == 1


def test_complete_commitment(env):
    c, ctx, db = env
    cid = c.executor.run(ctx, "propose_commitment", {"text": "x", "confirmed": True}).result["commitment"]["id"]
    out = c.executor.run(ctx, "complete_commitment", {"commitment_id": cid})
    assert out.result["commitment"]["status"] == "done"
    assert db.get(Commitment, cid).completed_at is not None


def test_list_commitments_flags_overdue(env):
    c, ctx, _ = env
    c.executor.run(ctx, "propose_commitment", {"text": "late one", "deadline": "2000-01-01", "confirmed": True})
    listed = c.executor.run(ctx, "list_commitments", {}).result["commitments"]
    assert listed[0]["overdue"] is True


# ---------- memory ----------
def test_save_memory_deduplicates_identical_content(env):
    c, ctx, db = env
    first = c.executor.run(ctx, "save_memory", {"content": "Prefers evening study sessions.", "kind": "preference"})
    second = c.executor.run(ctx, "save_memory", {"content": "Prefers evening study sessions.", "kind": "preference"})
    assert first.result["memory"]["id"] == second.result["memory"]["id"]
    assert second.result.get("note") == "Already stored (duplicate skipped)."
    assert db.query(Memory).count() == 1


def test_search_memories_by_keyword_and_kind(env):
    c, ctx, _ = env
    c.executor.run(ctx, "save_memory", {"content": "Studies best in the evening.", "kind": "preference"})
    c.executor.run(ctx, "save_memory", {"content": "Is applying to CS master's programs.", "kind": "context"})
    out = c.executor.run(ctx, "search_memories", {"query": "evening"})
    assert out.result["total"] == 1
    assert out.result["memories"][0]["kind"] == "preference"

    only_context = c.executor.run(ctx, "search_memories", {"kind": "context"})
    assert only_context.result["total"] == 1


def test_update_memory(env):
    c, ctx, db = env
    mid = c.executor.run(ctx, "save_memory", {"content": "old fact"}).result["memory"]["id"]
    c.executor.run(ctx, "update_memory", {"memory_id": mid, "content": "corrected fact", "importance": "high"})
    m = db.get(Memory, mid)
    assert (m.content, m.importance) == ("corrected fact", "high")


def test_delete_memory_is_high_impact_and_queued(env):
    c, ctx, db = env
    mid = c.executor.run(ctx, "save_memory", {"content": "temp fact"}).result["memory"]["id"]
    out = c.executor.run(ctx, "delete_memory", {"memory_id": mid})
    assert out.status == "pending_confirmation"
    assert db.get(Memory, mid) is not None  # not deleted yet

    c.executor.confirm(ctx, out.pending_action_id)
    assert db.get(Memory, mid) is None
