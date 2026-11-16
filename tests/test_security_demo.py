"""The security demo must pass on the real code and fail when a defence is removed, or it demonstrates nothing."""
import json

import pytest

from app.security.permissions import Decision, PermissionPolicy
from scripts import security_demo


def test_every_attack_in_the_demo_is_blocked(capsys):
    assert security_demo.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["held"] == out["total"] == len(security_demo.SCENARIOS) >= 12
    assert all(s["held"] for s in out["scenarios"])


def _held(capsys) -> dict[str, bool]:
    code = security_demo.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    assert (code == 0) == (out["held"] == out["total"])
    return {s["name"]: s["held"] for s in out["scenarios"]}


def test_the_demo_fails_when_the_permission_gate_is_removed(monkeypatch, capsys):
    monkeypatch.setattr(PermissionPolicy, "decide", lambda self, tool, *, tainted: Decision.ALLOW)
    held = _held(capsys)
    assert not held["Injected delete"] and not held["Injected write"] and not held["Memory poisoning"]
    assert not held["Instructions that outlive the page"]
    assert held["Reaching inside your network"] and held["Text you cannot see"]  # unrelated defences still hold


def test_the_demo_fails_when_the_browser_checks_are_removed(monkeypatch, capsys):
    monkeypatch.setattr("app.api.routes._browser_problem", lambda request: None)
    held = _held(capsys)
    assert not held["A website approves for you (CSRF)"] and not held["A website reads your data (DNS rebinding)"]
    assert held["Injected delete"]


@pytest.mark.parametrize("target, broken", [
    ("app.tools.web._may_fetch_while_tainted", ["Data smuggled in a URL", "Leak by choice of link"]),
    ("app.api.routes._direct_local", ["Someone on your network"]),
])
def test_the_demo_fails_when_other_single_defences_are_removed(monkeypatch, capsys, target, broken):
    monkeypatch.setattr(target, lambda *a, **k: True)
    held = _held(capsys)
    for name in broken:
        assert not held[name], name
