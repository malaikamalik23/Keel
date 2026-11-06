from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, text, tool_call


def client_with(responses):
    return TestClient(create_app(make_settings(), ScriptedLLM(responses)))


def test_health():
    assert client_with([]).get("/health").json() == {"status": "ok"}


def test_chat_creates_task_visible_via_tasks_endpoint():
    client = client_with([tool_call("create_task", {"title": "Write FYP intro", "estimated_minutes": 90}), text("Done.")])
    r = client.post("/api/chat", json={"message": "add: write FYP intro, 90 min"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Done." and body["tool_calls"][0]["status"] == "ok"
    tasks = client.get("/api/tasks").json()
    assert [t["title"] for t in tasks] == ["Write FYP intro"]


def test_confirm_flow_over_http():
    client = client_with([
        tool_call("create_task", {"title": "temp"}, id="a"), text("ok"),
        tool_call("delete_task", {"task_id": 1}, id="b"), text("Waiting for approval."),
    ])
    conv = client.post("/api/chat", json={"message": "add temp"}).json()["conversation_id"]
    body = client.post("/api/chat", json={"message": "delete it", "conversation_id": conv}).json()
    pid = body["pending_action_ids"][0]
    assert len(client.get("/api/tasks").json()) == 1

    assert client.get("/api/pending-actions").json()[0]["id"] == pid
    assert client.post(f"/api/pending-actions/{pid}/confirm").json()["status"] == "ok"
    assert client.get("/api/tasks").json() == []
    assert client.post(f"/api/pending-actions/{pid}/confirm").status_code == 409
    statuses = [a["status"] for a in client.get("/api/audit-log").json()]
    assert "pending_confirmation" in statuses and statuses.count("ok") >= 2


def test_reject_over_http():
    client = client_with([tool_call("create_task", {"title": "t"}, id="a"), text("ok"),
                          tool_call("delete_task", {"task_id": 1}, id="b"), text("pending")])
    conv = client.post("/api/chat", json={"message": "add"}).json()["conversation_id"]
    pid = client.post("/api/chat", json={"message": "del", "conversation_id": conv}).json()["pending_action_ids"][0]
    assert client.post(f"/api/pending-actions/{pid}/reject").json()["status"] == "rejected"
    assert len(client.get("/api/tasks").json()) == 1


def test_unknown_conversation_is_404_and_missing_key_is_502():
    client = client_with([])
    assert client.post("/api/chat", json={"message": "hi", "conversation_id": 99}).status_code == 404
    from app.llm.factory import UnconfiguredLLM
    no_key = TestClient(create_app(make_settings(), UnconfiguredLLM()))
    r = no_key.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_goal_project_commitment_memory_endpoints_reflect_agent_actions():
    client = client_with([
        tool_call("create_goal", {"title": "Web pentesting", "category": "skills"}, id="a"),
        text("Created the goal."),
        tool_call("create_project", {"name": "FYP"}, id="b"),
        text("Created the project."),
        tool_call("propose_commitment", {"text": "Study tonight", "confirmed": True}, id="c"),
        text("Noted."),
        tool_call("save_memory", {"content": "Prefers evening study sessions.", "kind": "preference"}, id="d"),
        text("Saved."),
    ])
    client.post("/api/chat", json={"message": "I want to get good at web pentesting"})
    client.post("/api/chat", json={"message": "add project FYP"})
    client.post("/api/chat", json={"message": "I'll study tonight"})
    client.post("/api/chat", json={"message": "I prefer studying in the evening"})

    assert [g["title"] for g in client.get("/api/goals").json()] == ["Web pentesting"]
    assert [p["name"] for p in client.get("/api/projects").json()] == ["FYP"]
    assert [c["text"] for c in client.get("/api/commitments").json()] == ["Study tonight"]
    assert [m["content"] for m in client.get("/api/memories").json()] == ["Prefers evening study sessions."]
