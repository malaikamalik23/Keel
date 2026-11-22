"""Google Calendar through the user's own Google sign-in (shared with Gmail). Events only: Keel never touches calendar
settings, sharing or other people's calendars, and never emails guests (every change is sent with sendUpdates=none).
Event text can come from other people's invites, so the tool that lists events is `returns_untrusted`."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app.integrations.google_auth import CALENDAR_SCOPE, GoogleAuth, GoogleError

API = "https://www.googleapis.com/calendar/v3"
KEEL_KEY = "keel"  # extendedProperties.private[keel] marks events Keel created (e.g. "plan:2026-11-25:12")


class CalendarWriteError(GoogleError):
    pass


def check_id(value: str) -> str:
    v = value.strip()
    if not v or len(v) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", v):
        raise CalendarWriteError("That isn't a Google Calendar event id.")
    return v


def parse_when(value: str, tz: ZoneInfo) -> tuple[datetime | date, bool]:
    """'2026-11-25T14:00' (a time, local) or '2026-11-25' (all day). Returns (value, all_day)."""
    v = value.strip()
    try:
        if "T" in v or " " in v:
            dt = datetime.fromisoformat(v.replace(" ", "T"))
            return (dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)), False
        return date.fromisoformat(v), True
    except ValueError:
        raise CalendarWriteError(f"'{v[:40]}' isn't a date (2026-11-25) or a date and time (2026-11-25T14:00).") from None


def _side(value: datetime | date, tz: ZoneInfo) -> dict:
    if isinstance(value, datetime):
        return {"dateTime": value.isoformat(timespec="seconds"), "timeZone": str(tz)}
    return {"date": value.isoformat()}


def event_body(*, title: str, start: datetime | date, end: datetime | date, tz: ZoneInfo, location: str | None = None,
               description: str | None = None, reminder_minutes: int | None = None, marker: str | None = None) -> dict:
    body: dict = {"summary": title, "start": _side(start, tz), "end": _side(end, tz)}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    if reminder_minutes is not None:
        body["reminders"] = {"useDefault": False, "overrides": [{"method": "popup", "minutes": reminder_minutes}]}
    if marker:
        body["extendedProperties"] = {"private": {KEEL_KEY: marker}}
    return body


def normalize(raw: dict, tz: ZoneInfo) -> dict:
    def when(side: dict) -> tuple[datetime | date, bool]:
        if "dateTime" in side:
            return datetime.fromisoformat(side["dateTime"].replace("Z", "+00:00")).astimezone(tz), False
        return date.fromisoformat(side.get("date", "1970-01-01")), True

    s, all_day = when(raw.get("start") or {})
    e, _ = when(raw.get("end") or {})
    others = [a for a in raw.get("attendees") or [] if not a.get("self")]
    out = {
        "event_id": raw.get("id", ""), "title": str(raw.get("summary", "(no title)"))[:200],
        "start": s.isoformat(timespec="minutes") if isinstance(s, datetime) else s.isoformat(),
        "end": e.isoformat(timespec="minutes") if isinstance(e, datetime) else e.isoformat(),
        "all_day": all_day, "minutes": 0 if all_day else int((e - s).total_seconds() // 60),
        "location": str(raw.get("location", ""))[:200] or None,
        "description": _visible(str(raw.get("description", "")))[:1000] or None,
        "guests": len(others), "organizer_is_you": bool((raw.get("organizer") or {}).get("self", True)),
        "recurring": bool(raw.get("recurringEventId")), "updated": raw.get("updated", ""),
        "created_by_keel": ((raw.get("extendedProperties") or {}).get("private") or {}).get(KEEL_KEY),
    }
    return out


def _visible(description: str) -> str:
    """Google stores invitation text as HTML, written by whoever sent the invite: keep only what a person would see."""
    if "<" not in description:
        return description
    from app.integrations.safe_http import html_to_text

    return html_to_text(description)[1]


class GoogleCalendar:
    def __init__(self, auth: GoogleAuth, calendar_id: str = "primary"):
        self.auth = auth
        self.calendar_id = calendar_id

    @property
    def can_write(self) -> bool:
        return self.auth.has_scope(CALENDAR_SCOPE)

    def _call(self, method: str, path: str, headers: dict | None = None, **kw) -> dict:
        if not self.can_write:
            raise CalendarWriteError("Google Calendar isn't connected yet: set GOOGLE_CALENDAR_ENABLED=true, then run "
                                     "`python -m scripts.gmail_connect` again and allow the calendar permission.")
        for attempt in (1, 2):
            try:
                resp = self.auth.http.request(method, f"{API}/calendars/{self.calendar_id}{path}",
                                              headers={**(headers or {}), "Authorization": f"Bearer {self.auth.token()}"}, **kw)
            except httpx.HTTPError as exc:
                raise CalendarWriteError(f"Google Calendar request failed ({type(exc).__name__}).") from None
            if resp.status_code != 401 or attempt == 2:
                break
            # Google stopped accepting the access token early (password change, security event). A 401 means the
            # request did nothing, so get a fresh token and send it once more; a revoked sign-in fails at refresh.
            self.auth.forget()
        if resp.status_code in (404, 410):
            raise CalendarWriteError("Google Calendar has no event with that id (it may have been deleted).")
        if resp.status_code == 401:
            self.auth.forget()
            raise CalendarWriteError("Google Calendar rejected the sign-in; try again.")
        if resp.status_code == 403:
            raise CalendarWriteError("Google Calendar refused (missing permission, or an event you can't edit).")
        if resp.status_code == 412:
            raise CalendarWriteError("The event changed in Google Calendar since it was read; nothing was changed.")
        if resp.status_code == 429:
            raise CalendarWriteError("Google Calendar's rate limit was reached; try again in a minute.")
        if resp.status_code >= 400:
            raise CalendarWriteError(f"Google Calendar error (HTTP {resp.status_code}).")
        return resp.json() if resp.content else {}

    def events(self, start: datetime, end: datetime, query: str | None = None, limit: int = 100) -> list[dict]:
        params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true", "orderBy": "startTime",
                  "maxResults": max(1, min(limit, 250))}
        if query:
            params["q"] = query
        items = self._call("GET", "/events", params=params).get("items") or []
        return [i for i in items if i.get("status") != "cancelled"]

    def get(self, event_id: str) -> dict:
        return self._call("GET", f"/events/{check_id(event_id)}")

    def insert(self, body: dict) -> dict:
        return self._call("POST", "/events", params={"sendUpdates": "none"}, json=body)

    def patch(self, event_id: str, body: dict, etag: str | None = None) -> dict:
        headers = {"If-Match": etag} if etag else {}
        return self._call("PATCH", f"/events/{check_id(event_id)}", params={"sendUpdates": "none"}, json=body, headers=headers)

    def delete(self, event_id: str, etag: str | None = None) -> None:
        headers = {"If-Match": etag} if etag else {}
        self._call("DELETE", f"/events/{check_id(event_id)}", params={"sendUpdates": "none"}, headers=headers)

    def clashes(self, start: datetime, end: datetime, tz: ZoneInfo, ignore_id: str | None = None) -> list[str]:
        """Times (never titles) of other timed events overlapping [start, end)."""
        out = []
        for raw in self.events(start - timedelta(minutes=1), end + timedelta(minutes=1), limit=50):
            ev = normalize(raw, tz)
            if ev["all_day"] or ev["event_id"] == ignore_id:
                continue
            s, e = datetime.fromisoformat(ev["start"]), datetime.fromisoformat(ev["end"])
            if s < end and e > start:
                out.append(f"{s:%H:%M}–{e:%H:%M}")
        return out
