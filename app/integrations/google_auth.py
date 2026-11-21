"""The user's Google sign-in, shared by Gmail and Google Calendar.

One refresh token (from `python -m scripts.gmail_connect`) in a local file under data/, never committed. Access tokens
live in memory only and are refreshed when they expire. Errors never include a token or the client secret.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 # nosec B105 - a public endpoint, not a password
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"  # events only: not calendar settings or sharing


class GoogleError(Exception):
    pass


class GoogleNotConnected(GoogleError):
    pass


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


def granted_scopes(path: Path) -> set[str]:
    saved = load_token(path)
    return set(str(saved.get("scope", "")).split()) if saved else set()


class GoogleAuth:
    def __init__(self, client_id: str, client_secret: str | None, token_path: Path, client: httpx.Client | None = None):
        self._client_id, self._secret = client_id, client_secret
        self.token_path = token_path
        self.http = client or httpx.Client(timeout=20)
        self._access: tuple[str, float] | None = None

    @property
    def connected(self) -> bool:
        return load_token(self.token_path) is not None

    def has_scope(self, scope: str) -> bool:
        saved = load_token(self.token_path)
        # Token files written before scopes were recorded came from the Gmail-only sign-in.
        granted = set(str(saved.get("scope", "")).split()) if saved and saved.get("scope") else set(GMAIL_SCOPES) if saved else set()
        return scope in granted

    def forget(self) -> None:
        self._access = None

    def token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        saved = load_token(self.token_path)
        if saved is None:
            raise GoogleNotConnected("Google isn't connected. Run `python -m scripts.gmail_connect` once.")
        form = {"client_id": self._client_id, "refresh_token": saved["refresh_token"], "grant_type": "refresh_token"}
        if self._secret:
            form["client_secret"] = self._secret
        try:
            resp = self.http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise GoogleError(f"Couldn't reach Google ({type(exc).__name__}).") from None
        if resp.status_code != 200:
            if resp.status_code in (400, 401):
                raise GoogleNotConnected("Google refused the saved sign-in (it was revoked or expired). "
                                         "Run `python -m scripts.gmail_connect` again.")
            raise GoogleError(f"Google sign-in failed (HTTP {resp.status_code}).")
        data = resp.json()
        self._access = (str(data["access_token"]), time.time() + float(data.get("expires_in", 3600)))
        return self._access[0]
