"""Timezone helpers. The DB stores naive UTC; "a day" always means a day in the user's timezone."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def local_day_start_utc(d: date, tz: ZoneInfo) -> datetime:
    """Naive-UTC datetime for 00:00 local on `d`."""
    return datetime.combine(d, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def window_utc(start: date, end_inclusive: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[start of `start`, start of the day after `end_inclusive`) as naive UTC."""
    return local_day_start_utc(start, tz), local_day_start_utc(end_inclusive + timedelta(days=1), tz)


def to_local_date(naive_utc: datetime, tz: ZoneInfo) -> date:
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(tz).date()


def local_to_naive_utc(aware_or_naive_local: datetime, tz: ZoneInfo) -> datetime:
    dt = aware_or_naive_local if aware_or_naive_local.tzinfo else aware_or_naive_local.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def naive_utc_to_local(naive_utc: datetime, tz: ZoneInfo) -> datetime:
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(tz)
