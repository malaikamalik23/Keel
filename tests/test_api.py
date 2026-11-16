
from app.api.main import create_app
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, text, tool_call, local_client


def client_with(responses):
    return local_client(create_app(make_settings(), ScriptedLLM(responses)))


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
    no_key = local_client(create_app(make_settings(), UnconfiguredLLM()))
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


def test_memory_edit_and_delete_over_http_are_audited_as_user():
    client = client_with([
        tool_call("save_memory", {"content": "Prefers evenings.", "kind": "preference"}), text("Saved."),
    ])
    client.post("/api/chat", json={"message": "I prefer evenings"})

    r = client.patch("/api/memories/1", json={"content": "Prefers late evenings.", "importance": "high"})
    assert r.status_code == 200 and r.json()["content"] == "Prefers late evenings." and r.json()["importance"] == "high"
    assert client.patch("/api/memories/1", json={}).status_code == 422
    assert client.patch("/api/memories/1", json={"kind": "bogus"}).status_code == 422
    assert client.patch("/api/memories/99", json={"content": "x"}).status_code == 404

    assert client.delete("/api/memories/1").status_code == 204
    assert client.get("/api/memories").json() == []
    assert client.delete("/api/memories/1").status_code == 404
    audit = client.get("/api/audit-log").json()
    assert {(a["tool_name"], a["actor"]) for a in audit} >= {("update_memory", "user"), ("delete_memory", "user")}


def test_commitment_edit_over_http_tracks_postponement_and_completion():
    client = client_with([
        tool_call("propose_commitment", {"text": "Finish GRE set", "deadline": "2026-11-10", "confirmed": True}),
        text("Noted."),
    ])
    client.post("/api/chat", json={"message": "I'll finish GRE by Tuesday"})

    r = client.patch("/api/commitments/1", json={"deadline": "2026-11-14"})
    assert r.status_code == 200 and r.json()["deadline"] == "2026-11-14" and r.json()["postponement_count"] == 1

    r = client.patch("/api/commitments/1", json={"status": "done"})
    assert r.json()["status"] == "done" and r.json()["completed_at"] is not None
    assert client.patch("/api/commitments/1", json={}).status_code == 422
    assert client.patch("/api/commitments/42", json={"status": "dropped"}).status_code == 404


def test_new_rest_routes_require_api_key_when_configured():
    from app.api.main import create_app
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    assert client.patch("/api/memories/1", json={"content": "x"}).status_code == 401
    assert client.delete("/api/memories/1").status_code == 401
    assert client.patch("/api/commitments/1", json={"status": "dropped"}).status_code == 401


def test_built_dashboard_is_served_without_shadowing_the_api(tmp_path):
    (tmp_path / "index.html").write_text("<html>dashboard</html>")
    from app.api.main import create_app
    client = local_client(create_app(make_settings(frontend_dist=str(tmp_path)), ScriptedLLM([])))
    assert "dashboard" in client.get("/").text
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/tasks").json() == []
    assert client.get("/api/does-not-exist").status_code == 404


def test_no_dashboard_mounted_when_dist_missing(tmp_path):
    from app.api.main import create_app
    client = local_client(create_app(make_settings(frontend_dist=str(tmp_path / "nope")), ScriptedLLM([])))
    assert client.get("/").status_code == 404


def test_rate_limit_applies_to_chat_only_and_resets():
    from app.api.ratelimit import RateLimiter
    client = client_with([text("ok")] * 10)
    client.app.state.limiter = RateLimiter(3)
    codes = [client.post("/api/chat", json={"message": "hi"}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert client.get("/api/tasks").status_code == 200  # reads are not limited


def test_rate_limiter_window_and_disable():
    from app.api.ratelimit import RateLimiter
    t = [0.0]
    rl = RateLimiter(2, clock=lambda: t[0])
    assert rl.check("a") is None and rl.check("a") is None and rl.check("a") is not None
    assert rl.check("b") is None  # separate clients
    t[0] = 61
    assert rl.check("a") is None
    assert RateLimiter(0).check("x") is None


def test_security_headers_present():
    r = client_with([]).get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client_with([]).get("/api/tasks").headers["cache-control"] == "no-store"
