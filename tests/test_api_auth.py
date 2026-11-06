from fastapi.testclient import TestClient

from app.api.main import create_app
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM


def test_no_api_key_configured_leaves_api_open():
    client = TestClient(create_app(make_settings(), ScriptedLLM([])))
    assert client.get("/api/tasks").status_code == 200


def test_health_never_requires_a_key():
    client = TestClient(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    assert client.get("/health").status_code == 200


def test_configured_api_key_blocks_requests_without_it():
    client = TestClient(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_configured_api_key_blocks_the_wrong_key():
    client = TestClient(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_configured_api_key_allows_the_right_key():
    client = TestClient(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


def test_api_key_is_required_on_post_routes_too():
    client = TestClient(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401
