"""Personal onboarding and the settings you change from the dashboard instead of .env.

First run: the dashboard asks for your name and how to address you, timezone and working hours, main goals and
responsibilities, preferences, and how reminders should reach you. Every question can be skipped, and everything can be
changed later from the same page.

- Personal settings (timezone, working hours, quiet hours, check-in times, the daily message budget) are stored in
  `user_settings` as "setting:<name>" and take priority over .env. They are applied when Keel builds its container, and
  the API rebuilds it straight after a change, so nothing needs a restart. Secrets are never set this way.
- Who you are, your responsibilities and preferences become memories (source "user", shown in the Memory tab); goals
  become goals. Nothing is sent anywhere.
"""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Goal, Memory, UserSetting, utcnow

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
STATE_KEY = "onboarding:state"  # "done" | "skipped"; absent = never answered
SETTING_PREFIX = "setting:"


def _tz(v):
    try:
        ZoneInfo(str(v))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"'{v}' isn't a time zone name (e.g. Asia/Karachi).") from None
    return str(v)


def _hhmm(v):
    if not isinstance(v, str) or not HHMM.match(v):
        raise ValueError(f"'{v}' isn't a time like 09:00.")
    return v


def _count(lo, hi):
    def check(v):
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise ValueError(f"must be a whole number from {lo} to {hi}.")
        return v
    return check


# The only settings the dashboard may change. Keys, tokens, addresses and switches that widen access are not here.
PERSONAL_SETTINGS = {
    "agent_timezone": _tz, "workday_start": _hhmm, "workday_end": _hhmm, "quiet_hours_start": _hhmm, "quiet_hours_end": _hhmm,
    "morning_checkin_time": _hhmm, "evening_checkin_time": _hhmm, "notification_max_per_day": _count(0, 20),
    "weekly_checkin_weekday": _count(0, 6),
}


def setting_overrides(db: Session) -> dict:
    rows = db.scalars(select(UserSetting).where(UserSetting.key.like(f"{SETTING_PREFIX}%"))).all()
    out = {}
    for r in rows:
        name = r.key.removeprefix(SETTING_PREFIX)
        if name in PERSONAL_SETTINGS:
            try:
                out[name] = PERSONAL_SETTINGS[name](r.value)
            except ValueError:
                continue  # a bad stored value never stops Keel from starting
    return out


def _put(db: Session, key: str, value) -> None:
    row = db.get(UserSetting, key)
    if row is None:
        db.add(UserSetting(key=key, value=value, updated_at=utcnow()))
    else:
        row.value, row.updated_at = value, utcnow()


def save_settings(db: Session, values: dict) -> dict:
    """Validate everything first, then store; an invalid value changes nothing."""
    clean = {}
    for name, value in values.items():
        if name not in PERSONAL_SETTINGS:
            raise ValueError(f"'{name}' can't be changed here.")
        clean[name] = PERSONAL_SETTINGS[name](value)
    for name, value in clean.items():
        _put(db, SETTING_PREFIX + name, value)
    db.commit()
    return clean


def _clean_lines(items, limit=10, length=300) -> list[str]:
    out = []
    for item in items or []:
        text = " ".join(str(item).split())[:length]
        if text and text.lower() not in {o.lower() for o in out}:
            out.append(text)
    return out[:limit]


def _remember(db: Session, content: str, *, kind: str, category: str, topic: str | None = None) -> None:
    """A memory the user typed themselves. A topic holds one answer, so a new name replaces the old one."""
    from app.tools.memory import clean_topic

    topic = clean_topic(topic) if topic else None
    existing = db.scalars(select(Memory).where(func.lower(Memory.topic) == topic)).first() if topic else \
        db.scalars(select(Memory).where(func.lower(Memory.content) == content.lower())).first()
    if existing is not None:
        existing.content, existing.kind, existing.category, existing.confirmed_at, existing.source = content, kind, category, utcnow(), "user"
        return
    db.add(Memory(content=content, kind=kind, category=category, topic=topic, source="user", importance="high" if topic else "medium",
                  confirmed_at=utcnow()))


def save_profile(db: Session, answers: dict) -> dict:
    """Store the onboarding answers. Unknown or empty answers are skipped; returns what was saved."""
    saved: dict = {"memories": 0, "goals": 0}
    name = " ".join(str(answers.get("name") or "").split())[:80]
    call = " ".join(str(answers.get("address_as") or "").split())[:80]
    if name:
        _remember(db, f"Name: {name}", kind="fact", category="personal", topic="name")
        saved["memories"] += 1
    if call:
        _remember(db, f"Likes to be addressed as {call}", kind="preference", category="personal", topic="addressed as")
        saved["memories"] += 1
    for text in _clean_lines(answers.get("responsibilities")):
        _remember(db, f"Responsibility: {text}", kind="context", category="general")
        saved["memories"] += 1
    for text in _clean_lines(answers.get("preferences")):
        _remember(db, text, kind="preference", category="personal")
        saved["memories"] += 1
    existing = {t.lower() for t in db.scalars(select(Goal.title))}
    for title in _clean_lines(answers.get("goals"), length=200):
        if title.lower() not in existing:
            db.add(Goal(title=title, horizon="long"))
            existing.add(title.lower())
            saved["goals"] += 1
    db.commit()
    return saved


def set_state(db: Session, state: str) -> None:
    if state not in ("done", "skipped"):
        raise ValueError("state must be done or skipped")
    _put(db, STATE_KEY, state)
    db.commit()


def profile(db: Session, settings) -> dict:
    """What the onboarding page shows: the current answers and settings, so it doubles as 'edit my profile'."""
    def topic(t):
        m = db.scalars(select(Memory).where(func.lower(Memory.topic) == t)).first()
        return m.content if m else None

    name, call = topic("name"), topic("addressed as")
    row = db.get(UserSetting, STATE_KEY)
    return {
        "state": row.value if row else "new",
        "name": name.removeprefix("Name: ") if name else "",
        "address_as": call.removeprefix("Likes to be addressed as ") if call else "",
        "goals": [g.title for g in db.scalars(select(Goal).where(Goal.status == "active").order_by(Goal.id))][:10],
        "responsibilities": [m.content.removeprefix("Responsibility: ") for m in db.scalars(
            select(Memory).where(Memory.content.like("Responsibility: %")).order_by(Memory.id))][:10],
        "preferences": [m.content for m in db.scalars(select(Memory).where(Memory.kind == "preference", Memory.topic.is_(None))
                                                       .order_by(Memory.id))][:10],
        "settings": {k: getattr(settings, k) for k in PERSONAL_SETTINGS},
        "overridden": sorted(setting_overrides(db)),
    }
