"""Code-side document analysis: outline, compare two versions, find dates/deadlines and action items.

These run on the indexed text, so they are deterministic and cheap, and every item carries the page it came from.
They only *find* things; the model decides what to tell the user, and nothing here creates tasks or reminders.
The text is still the document author's, so tools that return it are fenced as untrusted like read_note.
"""
from __future__ import annotations

import difflib
import re
from datetime import date

from app.db.models import Document

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?\s+" + _MON + r",?(?:\s+(\d{4}))?", re.I), "dmy"),
    (re.compile(r"\b" + _MON + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b", re.I), "mdy"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "slash"),  # read as day/month (Pakistan, UK); a day > 12 settles it
]
DEADLINE_CUE = re.compile(
    r"\b(deadline|due|submit\w*|submission|apply|application|closes?|closing|last date|no later than|before|by|until|expires?|registration)\b", re.I)
ACTION_START = re.compile(r"^\s*(?:[-*•]\s*)?(?:\[ \]|todo:?|action( item)?s?:|ai:|next steps?:|to do:)\s*", re.I)
ACTION_VERB = re.compile(
    r"\b(must|need(?:s)? to|should|please|required to|have to|has to|make sure|remember to|don'?t forget to)\b\s+\w+", re.I)
HEADING = re.compile(r"^(#{1,6}\s+.+|(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+[A-Z][^.!?]{2,80}|[A-Z][A-Z0-9 &,:'-]{3,80})$")


def _paragraphs(doc: Document) -> list[tuple[int | None, str]]:
    out = []
    for c in doc.chunks:
        for p in re.split(r"\n\s*\n", c.text):
            if p.strip():
                out.append((c.page, p.strip()))
    return out


def _lines(doc: Document) -> list[tuple[int | None, str]]:
    return [(page, line.strip()) for page, p in _paragraphs(doc) for line in p.splitlines() if line.strip()]


ABBREV = re.compile(r"(?:^|\s)(dr|mr|mrs|ms|prof|sr|jr|st|vs|e\.g|i\.e|etc|no|fig|eq|approx|dept|univ|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.$", re.I)


def _sentences(doc: Document) -> list[tuple[int | None, str]]:
    out = []
    for page, line in _lines(doc):
        merged: list[str] = []
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", line):
            if merged and ABBREV.search(merged[-1]):  # "Dr. Khan" is one sentence
                merged[-1] += " " + s
            else:
                merged.append(s)
        out += [(page, s.strip()) for s in merged if s.strip()]
    return out


def outline(doc: Document) -> dict:
    headings, words = [], 0
    for page, line in _lines(doc):
        words += len(line.split())
        if len(line) <= 90 and HEADING.match(line) and not line.endswith((",", ";")):
            item = {"heading": line.lstrip("# ").strip()}
            if page is not None:
                item["page"] = page
            headings.append(item)
    out = {"path": doc.path, "words": words, "headings": headings[:60]}
    if doc.pages:
        out["pages"] = doc.pages
        if doc.pages_estimated:
            out["pages_estimated"] = True
    if len(headings) > 60:
        out["more_headings"] = len(headings) - 60
    return out


def _resolve(kind: str, groups: tuple, today: date) -> date | None:
    try:
        if kind == "ymd":
            return date(int(groups[0]), int(groups[1]), int(groups[2]))
        if kind == "slash":
            a, b, y = int(groups[0]), int(groups[1]), int(groups[2])
            day, month = (a, b) if a > 12 or b <= 12 else (b, a)
            return date(y, month, day)
        if kind == "dmy":
            day, month, year = int(groups[0]), MONTHS[groups[1].lower()[:3]], groups[2]
        else:
            month, day, year = MONTHS[groups[0].lower()[:3]], int(groups[1]), groups[2]
        if year:
            return date(int(year), month, day)
        guess = date(today.year, month, day)  # no year: the next time that date comes round
        return guess if guess >= today else date(today.year + 1, month, day)
    except (ValueError, KeyError):
        return None


def find_dates(sentence: str, today: date) -> list[date]:
    found, spans = [], []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(sentence):
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue
            d = _resolve(kind, m.groups(), today)
            if d is not None:
                found.append(d)
                spans.append((m.start(), m.end()))
    return found


def extract_items(doc: Document, today: date, limit: int = 30) -> dict:
    deadlines, dates_only, actions, seen = [], [], [], set()
    for page, s in _sentences(doc):
        dates = find_dates(s, today)
        for d in dates:
            item = {"date": d.isoformat(), "text": s[:240], "days_left": (d - today).days}
            if page is not None:
                item["page"] = page
            (deadlines if DEADLINE_CUE.search(s) else dates_only).append(item)
        if ACTION_START.match(s) or ACTION_VERB.search(s):
            text = ACTION_START.sub("", s)[:240]
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                item = {"text": text}
                if page is not None:
                    item["page"] = page
                if dates:
                    item["by"] = min(dates).isoformat()
                actions.append(item)
    deadlines.sort(key=lambda x: x["date"])
    return {
        "path": doc.path, **({"pages_estimated": True} if doc.pages_estimated else {}),
        "deadlines": deadlines[:limit], "other_dates": dates_only[:limit], "action_items": actions[:limit],
        "note": "Found by pattern matching in the document; confirm with the user before creating tasks or reminders from them.",
    }


def compare(a: Document, b: Document, limit: int = 15) -> dict:
    pa, pb = _paragraphs(a), _paragraphs(b)
    ta, tb = [t for _, t in pa], [t for _, t in pb]
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    added, removed, changed = [], [], []

    def at(items, i):
        page, text = items[i]
        return {"text": text[:300], **({"page": page} if page is not None else {})}

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "delete":
            removed += [at(pa, i) for i in range(i1, i2)]
        elif op == "insert":
            added += [at(pb, j) for j in range(j1, j2)]
        elif op == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                i, j = i1 + k, j1 + k
                if i < i2 and j < j2 and difflib.SequenceMatcher(None, ta[i], tb[j]).ratio() >= 0.5:
                    changed.append({"before": at(pa, i), "after": at(pb, j)})
                else:
                    if i < i2:
                        removed.append(at(pa, i))
                    if j < j2:
                        added.append(at(pb, j))
    words_a, words_b = sum(len(t.split()) for t in ta), sum(len(t.split()) for t in tb)
    return {
        "a": a.path, "b": b.path, "similarity": round(sm.ratio(), 2),
        **({"pages_estimated": True} if a.pages_estimated or b.pages_estimated else {}), "words": {"a": words_a, "b": words_b},
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed), "same": sum(bl.size for bl in sm.get_matching_blocks())},
        "added": added[:limit], "removed": removed[:limit], "changed": changed[:limit],
    }
