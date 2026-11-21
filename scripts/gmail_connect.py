"""Connect Keel to your Google account once (Gmail, and Google Calendar if GOOGLE_CALENDAR_ENABLED=true), or disconnect it.
Run on the computer where Keel runs:

    python -m scripts.gmail_connect               # opens Google's sign-in page in your browser
    python -m scripts.gmail_connect --disconnect  # revokes Keel's access and deletes the saved sign-in

You need GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first, from an OAuth client of type "Desktop app" that you
create in your own Google Cloud project (docs/INSTALL.md walks through it). Keel asks for: read your mail, create
drafts / send mail, and (only with GOOGLE_CALENDAR_ENABLED=true) see and edit your calendar events. Sending an email or
changing an event still needs your approval inside Keel. Run it again after turning the calendar on.

The sign-in uses PKCE and a one-time listener on 127.0.0.1, so the code never passes through anyone else's server.
The saved refresh token goes to GMAIL_TOKEN_PATH (data/gmail_token.json by default; data/ is never committed).
Nothing here prints a token or the client secret.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import Settings
from app.integrations.google_auth import CALENDAR_SCOPE, GMAIL_SCOPES, REVOKE_URL, TOKEN_URL, load_token, save_token

SCOPES = GMAIL_SCOPES  # what older callers and tests mean by 'the scopes'

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def scopes_for(s: Settings) -> list[str]:
    return GMAIL_SCOPES + ([CALENDAR_SCOPE] if s.google_calendar_enabled else [])


def auth_url(client_id: str, redirect: str, state: str, challenge: str, scopes: list[str] = GMAIL_SCOPES) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(scopes),
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256",
    })


def wait_for_code(state: str, timeout: float = 300) -> tuple[str, int, threading.Event, dict]:
    """Start a one-shot listener on 127.0.0.1 (a random free port). Returns its redirect URL and where the code lands."""
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                result["error"] = "The reply didn't match this sign-in (state mismatch); start again."
            elif "error" in q:
                result["error"] = f"Google said: {q['error'][0][:80]}"
            else:
                result["code"] = q.get("code", [""])[0]
            ok = "code" in result
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(("Keel is connected to your Google account. You can close this tab." if ok else "Sign-in failed; see the terminal.").encode())
            done.set()

        def log_message(self, *args):  # keep the code out of the terminal
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = timeout
    threading.Thread(target=server.handle_request, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", server.server_port, done, result


def exchange(client_id: str, secret: str | None, code: str, verifier: str, redirect: str, http: httpx.Client | None = None,
             scopes: list[str] = GMAIL_SCOPES) -> dict:
    form = {"client_id": client_id, "code": code, "code_verifier": verifier, "redirect_uri": redirect, "grant_type": "authorization_code"}
    if secret:
        form["client_secret"] = secret
    resp = (http or httpx.Client(timeout=20)).post(TOKEN_URL, data=form)
    if resp.status_code != 200:
        raise RuntimeError(f"Google refused the sign-in code (HTTP {resp.status_code}). Start again.")
    data = resp.json()
    if not data.get("refresh_token"):
        raise RuntimeError("Google sent no refresh token. Remove Keel at myaccount.google.com/permissions and run this again.")
    granted = set(str(data.get("scope", "")).split())
    if not set(scopes) <= granted:
        raise RuntimeError("Not every permission was granted (tick every box on Google's page). Run this again.")
    return {"refresh_token": data["refresh_token"], "scope": data.get("scope", "")}


def disconnect(s: Settings, http: httpx.Client | None = None) -> str:
    path = Path(s.gmail_token_path)
    saved = load_token(path)
    if saved is None:
        return "Gmail wasn't connected."
    try:
        (http or httpx.Client(timeout=20)).post(REVOKE_URL, data={"token": saved["refresh_token"]})
    except httpx.HTTPError:
        pass  # deleting the file still disconnects Keel; revoke it at myaccount.google.com/permissions too
    path.unlink(missing_ok=True)
    return "Disconnected: Keel's Gmail access was revoked and the saved sign-in deleted."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    s = Settings()
    if "--disconnect" in args:
        print(disconnect(s))
        return 0
    if not s.gmail_client_id:
        print("Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first (see docs/INSTALL.md, 'Connect Gmail').")
        return 1
    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    redirect, _, done, result = wait_for_code(state)
    scopes = scopes_for(s)
    url = auth_url(s.gmail_client_id, redirect, state, challenge, scopes)
    print("Opening Google's sign-in page. If it doesn't open, paste this link into your browser:\n" + url)
    webbrowser.open(url)
    if not done.wait(300):
        print("No answer from Google within 5 minutes. Run this again.")
        return 1
    if "error" in result:
        print(result["error"])
        return 1
    try:
        token = exchange(s.gmail_client_id, s.gmail_client_secret, result["code"], verifier, redirect, scopes=scopes)
    except RuntimeError as exc:
        print(exc)
        return 1
    save_token(Path(s.gmail_token_path), token)
    what = "Gmail and Google Calendar" if CALENDAR_SCOPE in scopes else "Gmail"
    print(f"Connected to {what}. The sign-in is saved in {s.gmail_token_path} (keep it private; it is never committed). Restart Keel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
