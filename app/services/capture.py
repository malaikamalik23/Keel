"""Capture inbox: type anything, Keel suggests where it belongs, and nothing is saved until you approve.

Suggestions come from plain rules, not the model: instant, free, the same every time, and they can't be steered by
anything but your own words. You see why each suggestion was made and can change its type and fields before filing.

    "remind me", or a clock time ("at 6pm")    -> reminder (with the time)
    "remember that ...", "my X is ...", "I prefer"-> memory
    names an active project + update words     -> project update (blocker / decision / update)
    "idea", "save this", "for later", a link   -> note (in NOTES_DIR/inbox/)
    anything else                              -> task (with a due date if one was mentioned)

Filing runs the same tools as chat (create_task, create_reminder, save_memory, create_note, log_project_entry) as the
user, so validation, the audit log and the integration switches apply.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

KINDS = ("task", "reminder", "memory", "note", "project_update")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

_TIME = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b|\bat\s+(\d{1,2})(?::(\d{2}))?\b|\b(\d{1,2}):(\d{2})\b", re.I)
_REL = re.compile(r"\bin\s+(\d{1,3})\s*(minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I)
_DATE_DM = re.compile(r"\b(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*\b", re.I)
_DATE_MD = re.compile(r"\b(?:on\s+)?(" + "|".join(MONTHS) + r")[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_URL = re.compile(r"https?://\S+", re.I)


def _next_weekday(today: date, idx: int) -> date:
    """The coming one ("Friday" on a Monday is this Friday); the same weekday as today means a week from now."""
    ahead = (idx - today.weekday()) % 7 or 7
    return today + timedelta(days=ahead)


def parse_when(text: str, now: datetime) -> tuple[date | None, datetime | None, list[str]]:
    """(day, exact local time, the phrases that were understood). Unclear text gives (None, None, [])."""
    low = text.lower()
    today, found = now.date(), []
    day: date | None = None
    if m := _REL.search(low):
        n, unit = int(m.group(1)), m.group(2)[0]
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        when = now + delta
        found.append(m.group(0))
        return (when.date(), when if unit in "mh" else None, found)
    if m := _ISO.search(low):
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            found.append(m.group(0))
        except ValueError:
            pass
    for rx, d_first in ((_DATE_DM, True), (_DATE_MD, False)):
        if day is None and (m := rx.search(low)):
            dd, mon = (m.group(1), m.group(2)) if d_first else (m.group(2), m.group(1))
            try:
                day = date(today.year, MONTHS.index(mon[:3]) + 1, int(dd))
                day = day if day >= today else date(today.year + 1, day.month, day.day)
                found.append(m.group(0).strip())
            except ValueError:
                day = None
    if day is None:
        for word, offset in (("day after tomorrow", 2), ("parson", 2), ("tomorrow", 1), ("kal", 1), ("tonight", 0),
                             ("this evening", 0), ("today", 0), ("aaj", 0)):
            if re.search(rf"\b{word}\b", low):
                day, _ = today + timedelta(days=offset), found.append(word)
                break
    if day is None:
        for i, name in enumerate(WEEKDAYS):
            if m := re.search(rf"\b(next\s+)?{name}\b", low):
                day = _next_weekday(today, i)
                found.append(m.group(0))
                break
    at: time | None = None
    if m := _TIME.search(text):
        h = m.group(1) or m.group(4) or m.group(6)
        mins = m.group(2) or m.group(5) or m.group(7) or "0"
        ampm = (m.group(3) or "").lower().replace(".", "")
        hour = int(h) % 12 + (12 if ampm == "pm" else 0) if ampm else int(h)
        if not ampm and m.group(4) and (re.search(r"\b(tonight|evening|night|raat)\b", low) or 1 <= hour <= 6):
            hour += 12  # "at 9 tonight", "at 5" -> evening, not 5 in the morning
        if 0 <= hour <= 23 and 0 <= int(mins) <= 59:
            at = time(hour, int(mins))
            found.append(m.group(0).strip())
    elif re.search(r"\b(morning|subah)\b", low):
        at = time(9, 0)
    elif re.search(r"\b(tonight|evening|shaam)\b", low):
        at = time(19, 0)
    if at is not None:
        d = day or today
        when = datetime.combine(d, at, tzinfo=now.tzinfo)
        if day is None and when <= now:
            when += timedelta(days=1)  # "at 9am" said at 10am means tomorrow
        return (when.date(), when, found)
    return (day, None, found)


def _strip(text: str, phrases: list[str]) -> str:
    for p in sorted(phrases, key=len, reverse=True):
        text = re.sub(re.escape(p), " ", text, flags=re.I)
    text = re.sub(r"\b(remind me( to| about)?|please|can you|don'?t let me forget( to)?|subah|shaam|morning|tonight|this evening)\b",
                  " ", text, flags=re.I)
    text = re.sub(r"\s+([,.!?])", r"\1", " ".join(text.split())).strip(" ,.-:;")
    text = re.sub(r"^(i\s+(need|have|want|got)\s+to|i\s+(must|should)|need\s+to|have\s+to|to)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(by|on|at|before|for|until|till)$", "", text, flags=re.I)
    return text[:1].upper() + text[1:] if text else text


def _title(text: str, n: int = 200) -> str:
    t = " ".join(text.split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def suggest(text: str, now: datetime, projects: list[tuple[int, str]] | None = None, *, notes: bool = True) -> dict:
    """{"kind", "fields", "why", "alternatives"} for one captured line. `projects`: (id, name) of active projects."""
    raw = " ".join(text.split())
    low = raw.lower()
    day, at, phrases = parse_when(raw, now)
    projects = projects or []

    def out(kind, fields, why):
        alts = [k for k in KINDS if k != kind and (k != "note" or notes) and (k != "project_update" or projects)]
        return {"kind": kind, "fields": fields, "why": why, "alternatives": alts}

    if re.search(r"\bremind\b|\bdon'?t let me forget\b|\byaad dila\w*", low) or at is not None:
        body = _strip(raw, phrases) or raw
        if at is None:
            at = datetime.combine(day or now.date() + timedelta(days=1), time(9, 0), tzinfo=now.tzinfo)
            why = "You asked to be reminded; no time was given, so 9:00" + (" that day" if day else " tomorrow") + " (change it if you like)."
        else:
            why = f"Has a time ({', '.join(phrases)}), so a reminder."
        return out("reminder", {"text": body, "remind_at": at.strftime("%Y-%m-%dT%H:%M")}, why)

    if notes and (_URL.search(raw) or re.search(r"\b(idea|ideas|save (this|it|that)|for later|read later|article|quote|thought)\b", low)):
        title = re.sub(r"^(remember|save|note|keep)\s+(this|that|it)?\s*", "", _URL.sub("", raw).strip(), flags=re.I).strip(" :-")
        title = _title(title[:1].upper() + title[1:] if title else "Saved link", 80)
        return out("note", {"title": title, "content": raw}, "Looks like something to keep and read later, not a to-do.")

    if m := re.match(r"^\s*(?:remember( that|:)?|note that|fyi:?)\s+(.+)$", raw, re.I):
        content = m.group(2).strip()
        return out("memory", _memory_fields(content[:1].upper() + content[1:]), "Starts with “remember”: something to keep about you.")
    if re.match(r"^\s*(my|i prefer|i like|i hate|i usually|i always|i never|i'?m allergic|i'?ve decided)\b", low) and not re.search(r"\b(need to|have to|must|should|to do)\b", low):
        return out("memory", _memory_fields(raw), "A fact or preference about you.")

    for pid, name in projects:
        words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3]
        key = name.lower().split(":")[0].strip()
        if (key and re.search(rf"\b{re.escape(key)}\b", low)) or any(re.search(rf"\b{re.escape(w)}\b", low) for w in words[:2]):
            if re.search(r"\b(blocked|stuck|waiting (on|for)|can'?t proceed)\b", low):
                return out("project_update", {"project_id": pid, "project": name, "kind": "blocker", "text": raw}, f"Mentions {name} and something blocking it.")
            if re.search(r"\b(decided|chose|going with|switched to)\b", low):
                return out("project_update", {"project_id": pid, "project": name, "kind": "decision", "text": raw}, f"A decision about {name}.")
            if re.search(r"\b(done|finished|progress|update|got|working|started|submitted|sent)\b", low):
                return out("project_update", {"project_id": pid, "project": name, "kind": "update", "text": raw}, f"Progress on {name}.")

    fields: dict = {"title": _title(_strip(raw, phrases) or raw)}
    if day is not None:
        fields["due_date"] = day.isoformat()
    why = "Something to do" + (f", due {day:%a %d %b} ({', '.join(phrases)})." if day else "; no date was mentioned.")
    if m := re.search(r"\b(?:with|to|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", raw):
        why += f" It involves {m.group(1)}."
    return out("task", fields, why)


def _memory_fields(text: str) -> dict:
    low = text.lower()
    kind = "decision" if re.search(r"\b(decided|chose|going with)\b", low) else \
        "preference" if re.search(r"\b(prefer|like|hate|love|best|rather)\b", low) else "fact"
    return {"content": _title(text.strip().rstrip("."), 1000), "kind": kind}


def note_path(title: str, now: datetime) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", title.lower()))[:50] or "note"
    return f"inbox/{now:%Y-%m-%d}-{slug}.md"
