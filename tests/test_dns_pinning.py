"""DNS rebinding: the fetcher checks that a host is public, then connects. If the connection looked the host up again,
a hostile DNS server could answer with a public IP for the check and 127.0.0.1 (or the cloud metadata address) for the
connection. These tests use the real client the app builds, with the socket layer replaced so nothing touches the network.
"""
from __future__ import annotations

import httpcore
import pytest

from app.integrations.safe_http import FetchError, PinnedBackend, SafeFetcher, UnsafeURLError


class Rebinding:
    """A hostile resolver: public for the first lookup, internal afterwards."""

    def __init__(self, *answers: list[str]):
        self.answers = list(answers)
        self.lookups: list[str] = []

    def __call__(self, host: str) -> list[str]:
        self.lookups.append(host)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _Stream:
    def __init__(self, record: dict):
        self.record = record

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.record["sni"] = server_hostname
        raise httpcore.ConnectError("stop here: no real network in tests")

    def close(self):
        pass


@pytest.fixture
def sockets(monkeypatch):
    """Replace the real socket connect. Records where a connection *would* have gone."""
    record: dict = {"connects": []}

    def fake_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        record["connects"].append((host, port))
        if port == 443:
            return _Stream(record)
        raise httpcore.ConnectError("stop here: no real network in tests")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fake_connect)
    return record


@pytest.mark.parametrize("internal", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_a_second_dns_answer_pointing_inside_is_refused_before_connecting(sockets, internal):
    resolver = Rebinding(["93.184.216.34"], [internal])
    with pytest.raises(UnsafeURLError, match="not on the public internet"):
        SafeFetcher(resolver=resolver).fetch("http://rebind.example/")
    assert sockets["connects"] == []  # never connected anywhere
    assert resolver.lookups == ["rebind.example", "rebind.example"]  # checked, then re-checked at connect time


def test_the_connection_goes_to_the_checked_address_not_a_fresh_lookup(sockets):
    resolver = Rebinding(["93.184.216.34"])
    with pytest.raises(FetchError):
        SafeFetcher(resolver=resolver).fetch("http://example.com/page")
    assert sockets["connects"] == [("93.184.216.34", 80)]


def test_https_still_verifies_the_hostname_while_connecting_to_the_pinned_ip(sockets):
    with pytest.raises(FetchError):
        SafeFetcher(resolver=Rebinding(["93.184.216.34"])).fetch("https://example.com/page")
    assert sockets["connects"] == [("93.184.216.34", 443)]
    assert sockets["sni"] == "example.com"  # certificate is checked against the name, not the IP


def test_the_app_fetcher_is_pinned_and_ignores_environment_proxies():
    """httpx has no public hook for this, so guard against an upgrade silently removing the pinning."""
    client = SafeFetcher().client
    assert isinstance(client._transport._pool._network_backend, PinnedBackend)
    assert client._trust_env is False
