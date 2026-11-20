"""Time-blocking: fit the day's plan into the free time the calendar leaves, inside the user's working hours.

Only the *times* of calendar events are used here, never their titles: titles are written by whoever sent the invite,
and nothing in planning needs them. All-day events (holidays, birthdays, "working from home") don't block time.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
GAP_MINUTES = 5  # breathing room after each block and around meetings


@dataclass(frozen=True, order=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def busy_intervals(source: Callable[[], str] | None, day: date, tz: ZoneInfo) -> list[Interval] | None:
    """Timed calendar events on `day` as intervals, merged where they overlap. None if there is no calendar or it
    can't be read (planning then works as if the day were free, and says so)."""
    if source is None:
        return None
    from app.integrations.calendar import parse_events

    try:
        events = parse_events(source(), day, day, tz)
    except Exception:  # noqa: BLE001 - a broken calendar must not stop planning
        logger.warning("Calendar could not be read for planning")
        return None
    raw = sorted(
        Interval(datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]))
        for e in events if not e["all_day"] and e["minutes"] > 0
    ) if events else []
    return merge(raw)


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: i.start):
        if out and iv.start <= out[-1].end:
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out


def overlapping_events(source: Callable[[], str] | None, day: date, tz: ZoneInfo) -> int:
    """How many pairs of timed events on `day` overlap (double-booked), counted from times only."""
    if source is None:
        return 0
    from app.integrations.calendar import parse_events

    try:
        events = [e for e in parse_events(source(), day, day, tz) if not e["all_day"] and e["minutes"] > 0]
    except Exception:  # noqa: BLE001
        return 0
    spans = sorted((datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"])) for e in events)
    return sum(1 for i, a in enumerate(spans) for b in spans[i + 1:] if b[0] < a[1])


def _round_up(dt: datetime, minutes: int = 5) -> datetime:
    extra = (-dt.minute) % minutes
    return (dt + timedelta(minutes=extra)).replace(second=0, microsecond=0)


def free_slots(day: date, tz: ZoneInfo, workday: tuple[time, time], busy: list[Interval], now: datetime | None = None) -> list[Interval]:
    """Working hours on `day` minus busy time (with a short gap around each event), starting no earlier than `now`."""
    start = datetime.combine(day, workday[0], tzinfo=tz)
    end = datetime.combine(day, workday[1], tzinfo=tz)
    if now is not None and now.date() == day:
        start = max(start, _round_up(now.astimezone(tz)))
    gap = timedelta(minutes=GAP_MINUTES)
    slots, cursor = [], start
    for b in busy:
        if b.end + gap <= cursor:
            continue
        if b.start - gap > cursor:
            slots.append(Interval(cursor, min(b.start - gap, end)))
        cursor = max(cursor, b.end + gap)
        if cursor >= end:
            break
    if cursor < end:
        slots.append(Interval(cursor, end))
    return [s for s in slots if s.minutes > 0]


def place(blocks: list[tuple], slots: list[Interval]) -> tuple[dict[object, Interval], list[object]]:
    """Put each (key, minutes[, must_end_by]) block, in order, into the earliest free slot it fits, whole. A short break
    follows each block. Returns the placements and the keys that didn't fit anywhere (deferred, never squeezed)."""
    remaining = list(slots)
    placed: dict[object, Interval] = {}
    unplaced: list[object] = []
    gap = timedelta(minutes=GAP_MINUTES)
    for key, minutes, *deadline in blocks:
        need = timedelta(minutes=minutes)
        end_by = deadline[0] if deadline and deadline[0] is not None else None
        for i, s in enumerate(remaining):
            if s.end - s.start >= need and (end_by is None or s.start + need <= end_by - gap):
                placed[key] = Interval(s.start, s.start + need)
                remaining[i] = Interval(min(s.start + need + gap, s.end), s.end)
                break
        else:
            unplaced.append(key)
    return placed, unplaced
