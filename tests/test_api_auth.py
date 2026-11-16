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


# ---- without API_KEY, a website open in your browser must not be able to use the local API ----

def _app_with_pending_delete():
    from zoneinfo import ZoneInfo

    from app.db.models import Task
    from app.tools.base import ToolContext

    app = create_app(make_settings(), ScriptedLLM([]))
    c = app.state.container
    with c.session_factory() as db:
        db.add(Task(title="Thesis"))
        db.commit()
        pid = c.executor.run(ToolContext(db=db, tz=ZoneInfo("UTC")), "delete_task", {"task_id": 1}).pending_action_id
    return app, pid


def _tasks(app) -> int:
    from app.db.models import Task

    with app.state.container.session_factory() as db:
        return db.query(Task).count()


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.test"},
    {"Origin": "null"},  # sandboxed iframes and some redirects send this
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},  # another port or subdomain is still another site's code
    {"Origin": "https://evil.test", "Sec-Fetch-Site": "cross-site"},
])
def test_a_website_cannot_approve_a_pending_action_cross_site(headers):
    """CSRF: a page on another site POSTs with mode:'no-cors'. Browsers send that without asking."""
    app, pid = _app_with_pending_delete()
    client = local_client(app)
    r = client.post(f"/api/pending-actions/{pid}/confirm", headers=headers)
    assert r.status_code == 403
    assert _tasks(app) == 1
    assert client.post(f"/api/pending-actions/{pid}/reject", headers=headers).status_code == 403


@pytest.mark.parametrize("host", ["evil.test", "evil.test:8000", "127.0.0.1.evil.test", "localhost.evil.test:8000"])
def test_dns_rebinding_cannot_read_or_change_anything(host):
    """A hostile domain re-pointed at 127.0.0.1 makes the browser treat the API as that domain's own site."""
    app, pid = _app_with_pending_delete()
    client = local_client(app)
    assert client.get("/api/tasks", headers={"Host": host}).status_code == 403
    assert client.get("/api/memories", headers={"Host": host}).status_code == 403
    assert client.post(f"/api/pending-actions/{pid}/confirm", headers={"Host": host}).status_code == 403
    assert _tasks(app) == 1


@pytest.mark.parametrize("headers", [
    {},  # curl, the CLI, scripts: no Origin at all
    {"Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},  # the dashboard
    {"Origin": "http://localhost:5173", "Host": "localhost:5173", "Sec-Fetch-Site": "same-origin"},  # Vite dev proxy
    {"Host": "[::1]:8000", "Origin": "http://[::1]:8000"},
    {"Sec-Fetch-Site": "none"},  # typed into the address bar
])
def test_the_dashboard_and_local_tools_still_work(headers):
    app, pid = _app_with_pending_delete()
    client = local_client(app)
    assert client.get("/api/tasks", headers=headers).status_code == 200
    assert client.post(f"/api/pending-actions/{pid}/confirm", headers=headers).status_code == 200
    assert _tasks(app) == 0


def test_with_an_api_key_any_host_name_works_because_the_key_is_the_protection():
    """Behind a reverse proxy with its own domain. A website can't send the key: it doesn't know it."""
    client = local_client(create_app(make_settings(api_key="secret"), ScriptedLLM([])), client=("192.168.1.20", 40000))
    assert client.get("/api/tasks", headers={"X-API-Key": "secret", "Host": "keel.example.org"}).status_code == 200
