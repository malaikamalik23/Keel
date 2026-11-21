"""Gmail through Google's REST API with the user's own OAuth client: read, search, draft and (after approval) send.

The refresh token lives in a local file (GMAIL_TOKEN_PATH, inside data/, never committed); access tokens are kept in
memory only and refreshed when they expire. Errors never include a token, the client secret, or a message body.
Everything read from a mailbox was written by someone else, so the tools that expose it are `returns_untrusted`.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from pathlib import Path

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 # nosec B105 - a public endpoint, not a password
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]
MAX_BODY_CHARS = 12_000
MAX_ATTACH_BYTES = 10_000_000
ADDRESS = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")


class GmailError(Exception):
    pass


class GmailNotConnected(GmailError):
    pass


# ------------------------------------------------------------------ token file
def load_token(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("refresh_token") else None


def save_token(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # owner-only where the OS supports it
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


# ------------------------------------------------------------------ parsing
def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}


def _walk(part: dict):
    yield part
    for p in part.get("parts", []) or []:
        yield from _walk(p)


def body_text(payload: dict) -> str:
    """Plain text if the mail has it, else the HTML part reduced to text (no links, scripts or styles)."""
    plain, html = [], []
    for p in _walk(payload):
        data = (p.get("body") or {}).get("data")
        if not data or p.get("filename"):
            continue
        text = _b64decode(data).decode("utf-8", errors="replace")
        if p.get("mimeType") == "text/plain":
            plain.append(text)
        elif p.get("mimeType") == "text/html":
            html.append(text)
    if plain:
        return "\n".join(plain)
    if html:
        from app.integrations.safe_http import html_to_text

        return "\n".join(html_to_text(h)[1] for h in html)
    return ""


def strip_quoted(text: str) -> str:
    """Drop the quoted earlier messages ("On Mon, X wrote:" and "> " lines) so a thread isn't read three times."""
    out = []
    for line in text.splitlines():
        if re.match(r"^\s*On .{5,200}wrote:\s*$", line) or line.strip().startswith("-----Original Message-----"):
            break
        if not line.lstrip().startswith(">"):
            out.append(line)
    return "\n".join(out).strip()


def attachments(payload: dict) -> list[dict]:
    return [{"attachment_id": (p.get("body") or {}).get("attachmentId"), "name": p["filename"][:200], "type": p.get("mimeType", ""),
             "size": (p.get("body") or {}).get("size", 0)}
            for p in _walk(payload) if p.get("filename") and (p.get("body") or {}).get("attachmentId")]


CATEGORY = {"CATEGORY_PERSONAL": "primary", "CATEGORY_SOCIAL": "social", "CATEGORY_PROMOTIONS": "promotions",
            "CATEGORY_UPDATES": "updates", "CATEGORY_FORUMS": "forums"}


@dataclass
class Mail:
    id: str
    thread_id: str
    sender: str
    to: str
    cc: str
    subject: str
    date: str
    snippet: str
    labels: list[str] = field(default_factory=list)
    body: str = ""
    files: list[dict] = field(default_factory=list)
    message_id: str = ""  # the RFC 822 Message-ID, for replying in the same thread
    references: str = ""
    list_unsubscribe: bool = False

    @property
    def unread(self) -> bool:
        return "UNREAD" in self.labels

    @property
    def category(self) -> str:
        return next((CATEGORY[label] for label in self.labels if label in CATEGORY), "primary")

    def summary(self) -> dict:
        return {"id": self.id, "thread_id": self.thread_id, "from": self.sender, "subject": self.subject, "date": self.date,
                "snippet": self.snippet, "unread": self.unread, "category": self.category,
                **({"attachments": [f["name"] for f in self.files]} if self.files else {})}


def parse_message(raw: dict) -> Mail:
    payload = raw.get("payload") or {}
    h = _headers(payload)
    return Mail(
        id=str(raw.get("id", "")), thread_id=str(raw.get("threadId", "")), sender=h.get("from", "")[:200], to=h.get("to", "")[:500],
        cc=h.get("cc", "")[:500], subject=h.get("subject", "(no subject)")[:300], date=h.get("date", "")[:60],
        snippet=str(raw.get("snippet", ""))[:300], labels=list(raw.get("labelIds") or []),
        body=strip_quoted(body_text(payload))[:MAX_BODY_CHARS], files=attachments(payload),
        message_id=h.get("message-id", "")[:300], references=h.get("references", "")[:2000],
        list_unsubscribe="list-unsubscribe" in h,
    )


def check_addresses(value: str) -> list[str]:
    """Comma-separated plain addresses only: no display-name tricks, no header injection."""
    if any(c in value for c in "\r\n"):
        raise GmailError("Addresses can't contain line breaks.")
    out = []
    for _, addr in getaddresses([value]):
        addr = addr.strip()
        if not addr:
            continue
        if not ADDRESS.match(addr):
            raise GmailError(f"'{addr[:80]}' isn't an email address.")
        out.append(addr)
    return out


def build_mime(*, sender: str, to: list[str], cc: list[str], subject: str, body: str,
               files: list[tuple[str, bytes, str]] = (), reply_to: Mail | None = None) -> str:
    if any(c in subject for c in "\r\n"):
        raise GmailError("The subject can't contain line breaks.")
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if reply_to is not None and reply_to.message_id:
        msg["In-Reply-To"] = reply_to.message_id
        msg["References"] = (reply_to.references + " " + reply_to.message_id).strip()
    msg.set_content(body)
    for name, data, mime in files:
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


# ------------------------------------------------------------------ client
class GmailClient:
    def __init__(self, client_id: str, client_secret: str | None, token_path: Path, client: httpx.Client | None = None):
        self._client_id, self._secret = client_id, client_secret
        self.token_path = token_path
        self._http = client or httpx.Client(timeout=20)
        self._access: tuple[str, float] | None = None
        self._me: str | None = None

    @property
    def connected(self) -> bool:
        return load_token(self.token_path) is not None

    def _token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        saved = load_token(self.token_path)
        if saved is None:
            raise GmailNotConnected("Gmail isn't connected. Run `python -m scripts.gmail_connect` once.")
        form = {"client_id": self._client_id, "refresh_token": saved["refresh_token"], "grant_type": "refresh_token"}
        if self._secret:
            form["client_secret"] = self._secret
        try:
            resp = self._http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise GmailError(f"Couldn't reach Google ({type(exc).__name__}).") from None
        if resp.status_code != 200:
            if resp.status_code in (400, 401):
                raise GmailNotConnected("Google refused the saved sign-in (it was revoked or expired). "
                                        "Run `python -m scripts.gmail_connect` again.")
            raise GmailError(f"Google sign-in failed (HTTP {resp.status_code}).")
        data = resp.json()
        self._access = (str(data["access_token"]), time.time() + float(data.get("expires_in", 3600)))
        return self._access[0]

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            resp = self._http.request(method, API + path, headers={"Authorization": f"Bearer {self._token()}"}, **kw)
        except httpx.HTTPError as exc:
            raise GmailError(f"Gmail request failed ({type(exc).__name__}).") from None
        if resp.status_code == 404:
            raise GmailError("Gmail has no message, thread or attachment with that id.")
        if resp.status_code == 401:
            self._access = None
            raise GmailError("Gmail rejected the sign-in; try again.")
        if resp.status_code == 403:
            raise GmailError("Gmail refused the request (missing permission or the daily limit).")
        if resp.status_code == 429:
            raise GmailError("Gmail's rate limit was reached; try again in a minute.")
        if resp.status_code >= 400:
            raise GmailError(f"Gmail error (HTTP {resp.status_code}).")
        return resp.json() if resp.content else {}

    def me(self) -> str:
        if self._me is None:
            self._me = str(self._call("GET", "/profile").get("emailAddress", ""))
        return self._me

    def profile(self) -> dict:
        return self._call("GET", "/profile")

    def search(self, query: str, limit: int = 10) -> list[Mail]:
        ids = self._call("GET", "/messages", params={"q": query, "maxResults": max(1, min(limit, 50))}).get("messages") or []
        out = []
        for m in ids:
            raw = self._call("GET", f"/messages/{m['id']}", params=[("format", "metadata"), *[("metadataHeaders", h) for h in
                             ("From", "To", "Cc", "Subject", "Date", "List-Unsubscribe")]])
            out.append(parse_message(raw))
        return out

    def message(self, message_id: str) -> Mail:
        return parse_message(self._call("GET", f"/messages/{_id(message_id)}", params={"format": "full"}))

    def thread(self, thread_id: str) -> list[Mail]:
        data = self._call("GET", f"/threads/{_id(thread_id)}", params={"format": "full"})
        return [parse_message(m) for m in data.get("messages") or []]

    def attachment(self, message_id: str, attachment_id: str) -> bytes:
        data = self._call("GET", f"/messages/{_id(message_id)}/attachments/{_id(attachment_id, 1024)}")
        raw = _b64decode(str(data.get("data", "")))
        if len(raw) > MAX_ATTACH_BYTES:
            raise GmailError("That attachment is larger than 10 MB.")
        return raw

    def create_draft(self, raw: str, thread_id: str | None = None) -> dict:
        message: dict = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        return self._call("POST", "/drafts", json={"message": message})

    def send(self, raw: str, thread_id: str | None = None) -> dict:
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return self._call("POST", "/messages/send", json=body)

    def has_corresponded(self, address: str) -> bool:
        return bool(self._call("GET", "/messages", params={"q": f"from:{address} OR to:{address}", "maxResults": 1}).get("messages"))


def _id(value: str, max_len: int = 64) -> str:
    """Gmail ids are short base-16/64 strings; anything else could reach another API path."""
    v = value.strip()
    if not v or len(v) > max_len or not re.fullmatch(r"[A-Za-z0-9_-]+", v):
        raise GmailError("That isn't a Gmail id.")
    return v


def display_address(value: str) -> str:
    name, addr = parseaddr(value)
    return f"{name} <{addr}>" if name and addr else addr or value
