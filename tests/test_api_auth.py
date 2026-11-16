import pytest


from app.api.main import create_app
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM, local_client


def test_no_api_key_configured_still_serves_this_machine():
    client = local_client(create_app(make_settings(), ScriptedLLM([])))
    assert client.get("/api/tasks").status_code == 200


@pytest.mark.parametrize("peer", ["192.168.1.20", "10.0.0.5", "203.0.113.7", "2001:db8::1", "testclient"])
def test_no_api_key_refuses_anyone_not_on_this_machine(peer):
    """Started with --host 0.0.0.0 and no API_KEY: the API must not be open to the network."""
    client = local_client(create_app(make_settings(), ScriptedLLM([])), client=(peer, 40000))
    for r in [client.get("/api/tasks"), client.get("/api/memories"), client.post("/api/pending-actions/1/confirm")]:
        assert r.status_code == 403, peer
        assert "API_KEY" in r.json()["detail"]
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("header", ["X-Forwarded-For", "X-Real-IP", "Forwarded"])
def test_no_api_key_refuses_requests_relayed_by_a_local_reverse_proxy(header):
    """A proxy on the same machine connects from 127.0.0.1; the forwarding header gives it away."""
    client = local_client(create_app(make_settings(), ScriptedLLM([])))
    assert client.get("/api/tasks", headers={header: "203.0.113.7"}).status_code == 403


def test_with_an_api_key_remote_clients_are_fine():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])), client=("192.168.1.20", 40000))
    assert client.get("/api/tasks", headers={"X-API-Key": "secret"}).status_code == 200
    assert client.get("/api/tasks").status_code == 401


def test_health_never_requires_a_key():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    assert client.get("/health").status_code == 200


def test_configured_api_key_blocks_requests_without_it():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_configured_api_key_blocks_the_wrong_key():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_configured_api_key_allows_the_right_key():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.get("/api/tasks", headers={"X-API-Key": "secret"})
    assert r.status_code == 200


def test_api_key_is_required_on_post_routes_too():
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])))
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 401
