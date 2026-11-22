"""Keel's browser: the rules that don't need a real browser (which clicks are consequential, which fields are never
typed, the network guard, wiring). The real-Chromium tests are in tests/e2e/test_browser_tools.py."""
from __future__ import annotations

import pytest

from app.integrations.browser import BrowserError, BrowserSession
from app.services import connections
from app.tools import build_default_registry
from app.tools.base import Permission
from app.tools.browser import consequential, sensitive
from tests.conftest import make_settings


def el(tag="a", **kw):
    return {"ref": "e1", "tag": tag, "type": "", "text": "", "label": "", "name": "", "href": "", "in_form": False, "form_method": "", **kw}


@pytest.mark.parametrize(("element", "blocked"), [
    (el(text="Read the guidelines", href="https://x.test/guide"), False),
    (el(text="Next page", href="https://x.test/2"), False),
    (el("button", type="button", text="Show more fields", in_form=True, form_method="post"), False),
    (el("button", text="Search", in_form=True, form_method="get"), False),       # a search box: just navigation
    (el("button", text="Continue", in_form=True, form_method="post"), True),     # posts the form to the site
    (el("input", type="submit", text="Go", in_form=True, form_method="post"), True),
    (el(text="Delete my account", href="https://x.test/delete"), True),
    (el(text="Pay now", href="https://x.test/pay"), True),
    (el("button", type="button", text="Sign in"), True),
    (el(text="Register", href="https://x.test/r"), True),
    (el(text="Click me", href="javascript:doIt()"), True),
    (el("div", role="button", text="Confirm booking"), True),
])
def test_which_clicks_need_the_user(element, blocked):
    assert bool(consequential(element)) is blocked


@pytest.mark.parametrize(("field", "never"), [
    (el("input", label="Full name"), False), (el("input", label="Email", type="email"), False),
    (el("textarea", label="Statement of purpose"), False), (el("select", label="Country"), False),
    (el("input", type="password", label="Choose something"), True), (el("input", label="Card number"), True),
    (el("input", name="cvv"), True), (el("input", autocomplete="cc-exp"), True), (el("input", label="IBAN"), True),
    (el("input", label="CNIC number"), True), (el("input", label="Passport no."), True),
    (el("input", label="Enter the one-time code"), True), (el("input", autocomplete="one-time-code"), True),
])
def test_fields_keel_never_types(field, never):
    assert bool(sensitive(field)) is never


class FakeRoute:
    def __init__(self, url, method="GET"):
        self.request = type("R", (), {"url": url, "method": method, "post_data": None})()
        self.outcome = None

    def abort(self, reason):
        self.outcome = ("abort", reason)

    def continue_(self):
        self.outcome = ("continue", None)


def test_the_network_guard_keeps_pages_off_private_addresses():
    dns = {"public.example.test": ["93.184.216.34"], "rebind.example.test": ["127.0.0.1"], "metadata.example.test": ["169.254.169.254"]}
    s = BrowserSession(resolver=lambda h: dns.get(h, ["10.0.0.1"]))
    for url, allowed in [("https://public.example.test/app.js", True), ("http://127.0.0.1:8000/api/memories", False),
                         ("https://rebind.example.test/x", False), ("http://metadata.example.test/latest/meta-data", False),
                         ("http://[::1]:8000/", False), ("http://192.168.1.1/admin", False), ("file:///C:/Users/secret.txt", False),
                         ("data:text/plain,hi", True)]:
        route = FakeRoute(url)
        s._guard(route)
        assert (route.outcome[0] == "continue") is allowed, url
    assert "http://127.0.0.1:8000/api/memories" in s.blocked_requests
    with pytest.raises(BrowserError, match="public internet"):
        s.open("http://localhost/")  # refused before a browser even starts
    blocked = BrowserSession(blocked_domains=["evil.test"], resolver=lambda h: ["93.184.216.34"])
    with pytest.raises(BrowserError, match="blocked"):
        blocked.open("https://www.evil.test/")


def test_permissions_wiring_and_workflows():
    reg = build_default_registry(browser=BrowserSession(fixtures={}))
    assert {t.name for t in reg.all() if t.name.startswith("browser_")} == {
        "browser_open", "browser_read", "browser_click", "browser_fill", "browser_attach", "browser_submit", "browser_download", "browser_close"}
    for name in ("browser_fill", "browser_attach", "browser_submit"):
        assert reg.get(name).permission is Permission.HIGH_IMPACT and reg.get(name).summarize is not None
    for name in ("browser_open", "browser_read", "browser_click", "browser_fill", "browser_submit"):
        assert reg.get(name).returns_untrusted
    assert reg.get("browser_open").taint_gate is not None
    from app.services.workflows import validate_definition

    for step in ({"tool": "browser_submit", "args": {"ref": "e1"}, "approve": True}, {"tool": "browser_fill", "args": {"fields": [{"ref": "e1", "value": "x"}]}, "approve": True}):
        with pytest.raises(ValueError, match="not a tool workflows can use"):
            validate_definition(reg, {"type": "manual"}, [step])


def test_services_row():
    off = next(r for r in connections.describe(make_settings()) if r["id"] == "browser")
    assert off["status"] == "off" and not off["testable"]
    on = next(r for r in connections.describe(make_settings(browser_enabled=True, browser_headless=False)) if r["id"] == "browser")
    assert on["status"] == "connected" and on["testable"] and "Window visible" in on["details"][1]
    assert connections.run_check("browser", make_settings())["ok"] is None
