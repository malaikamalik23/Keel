"""A fetcher that is safe to point at attacker-influenced URLs.

Threat: the model is shown a poisoned page that says "fetch http://169.254.169.254/..." or "fetch
http://localhost:8000/api/memories". An unprotected fetch tool would turn the agent into a proxy into your
own network. Every hop is therefore validated *before* any request is made:

- http/https only, no credentials in the URL, only ports 80/443;
- the host must resolve to public addresses only (loopback, private, link-local, multicast, reserved, and
  IPv4-mapped forms of those are all refused -- this also catches decimal/octal/hex IP tricks, because the
  resolver sees the real address);
- redirects are followed manually (max 3) and each target is re-validated;
- response size is capped, content types are restricted to text-like ones, and timeouts are short;
- optional allow/deny lists of domains;
- DNS pinning: the connection goes to the exact address that was checked. The real client resolves the host once,
  inside the connect step, rejects it unless every address is public, and connects to that IP (TLS still verifies
  the certificate against the hostname). There is no second lookup for a hostile DNS server to answer differently
  (DNS rebinding). Environment proxies are ignored, since a proxy would do its own lookup and could reach your LAN.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

MAX_URL_LENGTH = 2000
ALLOWED_PORTS = {None, 80, 443}
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/rss+xml", "application/atom+xml")
REDIRECT_CODES = {301, 302, 303, 307, 308}
BLOCKED_SUFFIXES = (".local", ".localhost", ".internal", ".localdomain", ".lan", ".home.arpa")


class UnsafeURLError(Exception):
    """The URL is not allowed. The message is safe to show the model/user."""


class FetchError(Exception):
    pass


def default_resolver(host: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host '{host}'.") from exc


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _host_matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def validate_url(
    url: str, *, allowed_domains: Iterable[str] = (), blocked_domains: Iterable[str] = (),
    resolver: Callable[[str], list[str]] = default_resolver,
) -> str:
    """Return the URL if it is safe to request, else raise UnsafeURLError."""
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeURLError("URL is too long.")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed.")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    try:
        port = parts.port
    except ValueError:
        raise UnsafeURLError("Invalid port.") from None
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError("Only the default web ports (80/443) are allowed.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no host.")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES) or "." not in host and ":" not in host:
        raise UnsafeURLError("That address is not on the public internet.")
    blocked, allowed = [d.lower() for d in blocked_domains], [d.lower() for d in allowed_domains]
    if _host_matches(host, blocked):
        raise UnsafeURLError("That domain is blocked by configuration.")
    if allowed and not _host_matches(host, allowed):
        raise UnsafeURLError("That domain is not on the allowed list.")

    public_addresses(host, resolver)
    return parts.geturl()


def public_addresses(host: str, resolver: Callable[[str], list[str]] = default_resolver) -> list[str]:
    """The host's addresses, or UnsafeURLError unless every one of them is public."""
    host = host.strip("[]").rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
        addresses = [host]
    except ValueError:
        addresses = resolver(host)
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise UnsafeURLError("That address is not on the public internet (private, loopback or reserved network).")
    return addresses


class PinnedBackend(httpcore.SyncBackend):
    """Resolve and check the host at connect time, then connect to that exact address (closes DNS rebinding)."""

    def __init__(self, resolver: Callable[[str], list[str]] = default_resolver):
        self.resolver = resolver

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        ip = public_addresses(host, self.resolver)[0]
        return super().connect_tcp(ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options)


def pinned_client(resolver: Callable[[str], list[str]] = default_resolver, timeout: float = 10.0) -> httpx.Client:
    transport = httpx.HTTPTransport()
    pool = getattr(transport, "_pool", None)
    if not isinstance(pool, httpcore.ConnectionPool) or not hasattr(pool, "_network_backend"):
        # httpx has no public hook for this; fail loudly (a test covers it) rather than silently lose the pinning.
        raise RuntimeError("Unsupported httpx version: cannot pin DNS for the web fetcher.")
    pool._network_backend = PinnedBackend(resolver)
    return httpx.Client(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False,
        headers={"User-Agent": "Keel/0.1 (personal agent; read-only)"},
    )


# ----------------------------------------------------------------------------- HTML -> text
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object", "embed", "canvas", "head"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr"}
_BLOCK = {"p", "div", "section", "article", "header", "footer", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "br", "hr"}


# Inline styles that make text invisible to a person. Stylesheets and classes can't be seen without rendering the page
# (Keel's browser does that, see app/integrations/browser.py); these cover what emails, invites and simple pages use.
_HIDING_STYLE = re.compile(
    r"display:none|visibility:(hidden|collapse)|opacity:0?\.?0*(;|$|!)|font-size:0*\.?[01](\.\d+)?(px|pt)?(;|$|!)|font-size:0"
    r"|color:(transparent|rgba\([^)]*,0?\.?0*\))|(left|top|text-indent):-\d{3,}|clip:rect\(0[^)]*\)|mso-hide:all"
)
_ZERO_BOX = re.compile(r"(^|;)(max-)?(height|width):0*\.?0*(px|em|rem|%)?(;|$)")


def _hides(style: str) -> bool:
    if _HIDING_STYLE.search(style):
        return True
    return bool(_ZERO_BOX.search(style)) and "overflow:hidden" in style


class _TextExtractor(HTMLParser):
    """Visible text only. Drops scripts/styles and elements hidden from humans (hidden attr, aria-hidden, and inline
    styles: display:none, visibility, opacity 0, zero or tiny font, transparent colour, off-page positions, clipping,
    zero-size boxes) -- a favourite place to stash instructions aimed at AI agents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._stack: list[tuple[str, bool]] = []
        self._in_title = False

    def _skipped(self) -> bool:
        return bool(self._stack and self._stack[-1][1])

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag in _VOID:
            if tag in _BLOCK and not self._skipped():
                self.parts.append("\n")
            return
        style = a.get("style", "").replace(" ", "").lower()
        hidden = "hidden" in a or a.get("aria-hidden", "").lower() == "true" or _hides(style)
        self._stack.append((tag, self._skipped() or hidden or (tag in _SKIP_TAGS and tag != "head")))
        if tag in _BLOCK and not self._skipped():
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        for i in range(len(self._stack) - 1, -1, -1):  # tolerate malformed nesting
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break
        if tag in _BLOCK and not self._skipped():
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skipped():
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Returns (title, visible_text)."""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001, S110 - malformed HTML must never crash a tool; partial text is still useful
        pass  # nosec B110
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return " ".join(p.title.split())[:200], "\n".join(out).strip()


# ----------------------------------------------------------------------------- fetcher
@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    title: str
    text: str
    truncated: bool


class SafeFetcher:
    def __init__(
        self, *, allowed_domains: Iterable[str] = (), blocked_domains: Iterable[str] = (),
        client: httpx.Client | None = None, resolver: Callable[[str], list[str]] = default_resolver,
        max_bytes: int = 1_000_000, max_redirects: int = 3, timeout: float = 10.0,
    ):
        self.allowed, self.blocked, self.resolver = list(allowed_domains), list(blocked_domains), resolver
        self.max_bytes, self.max_redirects = max_bytes, max_redirects
        self.client = client or pinned_client(resolver, timeout)

    def _check(self, url: str) -> str:
        return validate_url(url, allowed_domains=self.allowed, blocked_domains=self.blocked, resolver=self.resolver)

    def fetch(self, url: str) -> FetchResult:
        current = self._check(url)
        for _ in range(self.max_redirects + 1):
            try:
                with self.client.stream("GET", current, headers={"Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1"}) as resp:
                    if resp.status_code in REDIRECT_CODES:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError("Redirect without a location.")
                        current = self._check(urljoin(current, location))  # re-validate every hop
                        continue
                    if resp.status_code != 200:
                        raise FetchError(f"The server answered HTTP {resp.status_code}.")
                    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    if not ctype.startswith(TEXT_TYPES):
                        raise FetchError(f"Unsupported content type '{ctype or 'unknown'}' (text pages only).")
                    body, truncated = bytearray(), False
                    for chunk in resp.iter_bytes():
                        body += chunk
                        if len(body) > self.max_bytes:
                            body, truncated = body[: self.max_bytes], True
                            break
                    text = bytes(body).decode(resp.encoding or "utf-8", errors="replace")
            except httpx.HTTPError as exc:
                raise FetchError(f"Request failed ({type(exc).__name__}).") from None
            title = ""
            if ctype in ("text/html", "application/xhtml+xml"):
                title, text = html_to_text(text)
            return FetchResult(url, current, 200, ctype, title, text, truncated)
        raise FetchError("Too many redirects.")
