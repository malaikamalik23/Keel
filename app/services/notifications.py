"""The notification budget. Decides, in code, whether a message may be sent right now.

Order of checks: duplicate -> priority threshold -> quiet hours -> daily budget -> (send). Reminders the user
asked for explicitly are exempt from threshold, quiet hours and budget; they only get dedupe and retry backoff.
The `notifications` table is the source of truth for all of it, so the limits survive restarts.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Notification
from app.integrations.notifier import NotificationError, Notifier
from app.services.timeutil import local_to_naive_utc, window_utc

logger = logging.getLogger(__name__)
PRIORITY = {"low": 0, "normal": 1, "high": 2}
RETRY_BACKOFF = timedelta(minutes=10)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def in_quiet_hours(t: time, start: time, end: time) -> bool:
    if start == end:
        return False
    return start <= t < end if start < end else (t >= start or t < end)


@dataclass
class SendResult:
    sent: bool
    reason: str | None = None  # why it was not sent
    text: str | None = None  # what was sent


class NotificationService:
    def __init__(self, notifier: Notifier, settings: Settings):
        self.notifier = notifier
        self.settings = settings
        self.tz = ZoneInfo(settings.agent_timezone)

    def sent_today(self, db: Session, now_local: datetime, kind: str | None = None) -> int:
        """Messages sent on `now_local`'s day, excluding reminders (which are exempt). Optionally only one kind."""
        lo, hi = window_utc(now_local.date(), now_local.date(), self.tz)
        stmt = select(func.count()).select_from(Notification).where(
            Notification.status == "sent", Notification.kind != "reminder", Notification.sent_at >= lo, Notification.sent_at < hi
        )
        if kind:
            stmt = stmt.where(Notification.kind == kind)
        return db.scalar(stmt) or 0

    def check(self, db: Session, *, kind: str, priority: str, dedupe_key: str | None, now_local: datetime) -> str | None:
        """Return the reason this would be blocked, or None if it may be sent."""
        if dedupe_key:
            if db.scalars(select(Notification).where(Notification.dedupe_key == dedupe_key, Notification.status == "sent")).first():
                return "duplicate"
            recent_fail = db.scalars(
                select(Notification).where(Notification.dedupe_key == dedupe_key, Notification.status == "failed", Notification.sent_at > local_to_naive_utc(now_local, self.tz) - RETRY_BACKOFF)
            ).first()
            if recent_fail:
                return "retry_backoff"
        if kind == "reminder":
            return None
        if PRIORITY[priority] < PRIORITY[self.settings.notification_min_priority]:
            return "below_priority_threshold"
        if priority != "high" and in_quiet_hours(
            now_local.time(), parse_hhmm(self.settings.quiet_hours_start), parse_hhmm(self.settings.quiet_hours_end)
        ):
            return "quiet_hours"
        if self.sent_today(db, now_local) >= self.settings.notification_max_per_day:
            return "daily_budget_reached"
        # Nudges draw from their own pool (max minus the slots reserved for scheduled check-ins), so a busy
        # afternoon of nudges can never crowd out the evening check-in.
        if kind == "nudge" and self.sent_today(db, now_local, kind="nudge") >= max(
            self.settings.notification_max_per_day - self.settings.checkin_reserve, 0
        ):
            return "daily_budget_reached"
        return None

    def send(
        self, db: Session, *, kind: str, text: str | Callable[[], str], priority: str = "normal",
        dedupe_key: str | None = None, now_local: datetime | None = None,
    ) -> SendResult:
        """`text` may be a callable so an LLM is only asked to word a message that is actually going out."""
        now_local = now_local or datetime.now(self.tz)
        blocked = self.check(db, kind=kind, priority=priority, dedupe_key=dedupe_key, now_local=now_local)
        if blocked:
            return SendResult(False, blocked)
        body = text() if callable(text) else text
        row = Notification(
            kind=kind, priority=priority, channel=self.notifier.channel, text=body, dedupe_key=dedupe_key,
            sent_at=local_to_naive_utc(now_local, self.tz),
        )
        try:
            self.notifier.send(body)
            row.status = "sent"
        except NotificationError as exc:
            row.status = "failed"
            logger.warning("Notification delivery failed: %s", exc)
        db.add(row)
        db.commit()
        return SendResult(row.status == "sent", None if row.status == "sent" else "delivery_failed", body)
