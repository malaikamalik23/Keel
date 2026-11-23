"""People memory: everything Keel knows about someone, gathered by name.

A person row holds who they are and when the user last talked to them. Promises to them, things the user is waiting on
them for, tasks about them and memories that mention them are found by matching their name (or an alias, or their first
name) as a whole word in the user's own commitments, tasks and memories. Nothing has to be linked by hand, and nothing
here reads email or the web.
"""
from __future__ import annotations

import re
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Commitment, Memory, Person, PersonInteraction, Task
from app.services.task_state import OPEN

# Titles are not names: "Dr. Khan" must not match every "Dr." in the user's tasks.
_TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof", "professor", "sir", "madam", "maam", "ma'am", "sahab", "sahib", "uncle", "aunty", "auntie", "bhai", "baji", "api"}


def name_variants(p: Person) -> list[str]:
    """How the user might refer to this person in their own words: full name, aliases, and the first name."""
    names = [p.name, *(a for a in (p.aliases or "").split(","))]
    out: list[str] = []
    for n in names:
        n = " ".join(n.split())
        if len(n) >= 2:
            out.append(n)
        words = [w for w in re.split(r"[\s.]+", n) if w and w.casefold() not in _TITLES]
        if len(words) >= 2 and len(words[0]) >= 3:
            out.append(words[0])  # "Sara Ahmed" -> "Sara"
        if len(words) == 1 and words[0] != n and len(words[0]) >= 3:
            out.append(words[0])  # "Dr. Khan" -> "Khan"
    seen: set[str] = set()
    return [v for v in out if not (v.casefold() in seen or seen.add(v.casefold()))]


def _pattern(p: Person) -> re.Pattern[str]:
    if not name_variants(p):
        return re.compile(r"(?!x)x")  # matches nothing: an empty alternation would match every text
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(v) for v in name_variants(p)) + r")(?!\w)", re.IGNORECASE)


def mentions(p: Person, text: str | None) -> bool:
    return bool(text) and bool(_pattern(p).search(text))


def find(db: Session, name: str) -> Person | None:
    """The person called `name` (or with that alias), ignoring case and extra spaces."""
    key = " ".join(name.split()).casefold()
    if not key:
        return None
    exact = db.scalars(select(Person).where(func.lower(Person.name) == key)).first()
    if exact:
        return exact
    for p in db.scalars(select(Person)):
        if key in {v.casefold() for v in name_variants(p)}:
            return p
    return None


def days_since(p: Person, today: date) -> int | None:
    return (today - p.last_contact).days if p.last_contact else None


def reconnect_due(p: Person, today: date) -> bool:
    """True when the user asked to keep in touch and it has been longer than that (or they never logged a contact)."""
    if not p.keep_in_touch_days:
        return False
    since = days_since(p, today)
    return since is None or since >= p.keep_in_touch_days


def person_dict(p: Person, today: date) -> dict:
    d = {
        "id": p.id, "name": p.name, "aliases": [a.strip() for a in (p.aliases or "").split(",") if a.strip()],
        "relation": p.relation, "notes": p.notes, "email": p.email, "keep_in_touch_days": p.keep_in_touch_days,
        "last_contact": p.last_contact.isoformat() if p.last_contact else None, "days_since_contact": days_since(p, today),
    }
    if reconnect_due(p, today):
        d["reconnect_due"] = True
    return d


def connected(db: Session, p: Person, today: date) -> dict:
    """Open promises to them, what the user waits on them for, open tasks about them, and memories that mention them."""
    rx = _pattern(p)
    promises = [
        {"id": c.id, "text": c.text, "deadline": c.deadline.isoformat() if c.deadline else None, "status": c.status,
         **({"overdue": True} if c.deadline and c.deadline < today else {})}
        for c in db.scalars(select(Commitment).where(Commitment.status.in_(("proposed", "open"))).order_by(Commitment.deadline, Commitment.id))
        if rx.search(c.text)
    ]
    open_tasks = list(db.scalars(select(Task).where(Task.status.in_(OPEN)).order_by(Task.due_date, Task.id)))
    waiting = [
        {"id": t.id, "title": t.title, "follow_up_date": t.follow_up_date.isoformat() if t.follow_up_date else None,
         **({"follow_up_due": True} if t.follow_up_date and t.follow_up_date <= today else {})}
        for t in open_tasks if t.waiting_on and rx.search(t.waiting_on)
    ]
    waiting_ids = {w["id"] for w in waiting}
    tasks = [
        {"id": t.id, "title": t.title, "due": t.due_date.isoformat() if t.due_date else None, "priority": t.priority}
        for t in open_tasks if t.id not in waiting_ids and rx.search(t.title)
    ]
    memories = [
        {"id": m.id, "content": m.content}
        for m in db.scalars(select(Memory).where(Memory.status == "current").order_by(Memory.id)) if rx.search(m.content)
    ][:10]
    return {"promises": promises, "waiting_on_them": waiting, "tasks": tasks, "memories": memories}


def interactions(db: Session, p: Person, limit: int = 20) -> list[dict]:
    rows = db.scalars(select(PersonInteraction).where(PersonInteraction.person_id == p.id)
                      .order_by(PersonInteraction.on_date.desc(), PersonInteraction.id.desc()).limit(limit))
    return [{"id": i.id, "date": i.on_date.isoformat(), "text": i.text} for i in rows]


def refresh_last_contact(db: Session, p: Person) -> None:
    """last_contact = the latest logged interaction (so removing a mistaken one puts it back)."""
    db.flush()
    p.last_contact = db.scalar(select(func.max(PersonInteraction.on_date)).where(PersonInteraction.person_id == p.id))


def summary(db: Session, p: Person, today: date) -> dict:
    """The full picture: who they are, the last time you spoke, what you promised them, what you're waiting on."""
    return {**person_dict(p, today), **connected(db, p, today), "interactions": interactions(db, p)}


def overview(db: Session, today: date) -> list[dict]:
    """Everyone, with counts of open promises and waits, people to get back in touch with first."""
    people = list(db.scalars(select(Person).order_by(Person.name)))
    out = []
    for p in people:
        c = connected(db, p, today)
        out.append({**person_dict(p, today), "promises": len(c["promises"]), "waiting": len(c["waiting_on_them"]),
                    "overdue_promises": sum(1 for x in c["promises"] if x.get("overdue")),
                    "follow_ups_due": sum(1 for x in c["waiting_on_them"] if x.get("follow_up_due"))})
    out.sort(key=lambda d: (not d.get("reconnect_due"), -(d["overdue_promises"] + d["follow_ups_due"]), d["name"].casefold()))
    return out
