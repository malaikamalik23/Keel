"""The scheduler. `tick()` is idempotent and self-catching-up, which is what makes it robust:

Every tick (default each minute) it looks at the *current state* -- due reminders, check-ins whose time has
passed and haven't been sent today, detector findings -- and acts on what is due. Nothing depends on a job
firing at an exact instant, so a laptop that was asleep at 08:00 still gets its morning check-in at 08:20
(within the grace window), and a tick that runs twice sends nothing twice (dedupe keys + one Checkin row per day).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.loop import Agent
from app.config import Settings
from app.db.models import Checkin, Conversation, Reminder, utcnow
from app.integrations.notifier import Notifier, TelegramNotifier
from app.integrations.telegram_bot import TelegramInbox
from app.scheduler.checkins import build_facts, checkins_due, fallback_text
from app.scheduler.detectors import detect_nudges
from app.services.briefing import calendar_events, calendar_lines, calendar_warnings
from app.services.notifications import NotificationService, parse_hhmm
from app.services.timeutil import local_to_naive_utc

logger = logging.getLogger(__name__)
CALENDAR_EVERY = timedelta(minutes=30)
REMINDER_MAX_LATE = timedelta(hours=24)
RESPONSE_WINDOW = timedelta(hours=6)


def latest_conversation_id(db: Session) -> int | None:
    return db.scalars(select(Conversation.id).order_by(Conversation.id.desc())).first()


class Scheduler:
    def __init__(self, *, settings: Settings, session_factory: sessionmaker[Session], agent: Agent,
                 notifications: NotificationService, notifier: Notifier, calendar_source=None):
        self.settings = settings
        self.calendar_source = calendar_source  # () -> iCal text, or None when no calendar is configured
        self.session_factory = session_factory
        self.agent = agent
        self.notifications = notifications
        self.notifier = notifier
        self.tz = ZoneInfo(settings.agent_timezone)
        self._aps = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._calendar_checked_at: datetime | None = None  # the calendar is re-read at most every CALENDAR_EVERY

    # ------------------------------------------------------------------ one pass
    def tick(self, now: datetime | None = None) -> dict:
        now_local = now.astimezone(self.tz) if now and now.tzinfo else (now.replace(tzinfo=self.tz) if now else datetime.now(self.tz))
        report: dict = {"reminders_sent": 0, "checkins_sent": [], "nudges_sent": 0, "blocked": {}}
        with self.session_factory() as db:
            for step in (self._reminders, self._calendar, self._checkins, self._nudges):
                try:
                    step(db, now_local, report)
                except Exception:  # noqa: BLE001 - one failing step must not stop the others
                    db.rollback()
                    logger.exception("Scheduler step %s failed", step.__name__)
        return report

    def _calendar(self, db: Session, now_local: datetime, report: dict) -> None:
        """Follow the calendar: linked events that moved or vanished, and today's blocks that a new meeting now hits.
        Event times only; the messages are written by code from the user's own task titles."""
        if self.calendar_source is None:
            return
        if self._calendar_checked_at and now_local - self._calendar_checked_at < CALENDAR_EVERY:
            return
        self._calendar_checked_at = now_local
        from app.services.event_links import sync_links
        from app.services.replan import refit_today
        from app.services.scheduling import busy_intervals
        from app.tools.planning import get_plan

        for ch in sync_links(db, self.calendar_source, self.tz):
            if ch["change"] == "moved":
                when = datetime.fromisoformat(ch["now"])
                text = f"The meeting your task “{ch['title']}” is for moved to {when:%a %b %d, %H:%M}. The task's due date moved with it."
            else:
                text = f"The meeting your task “{ch['title']}” was for is no longer on your calendar. Is the task still needed?"
            self.notifications.send(db, kind="nudge", priority="normal", dedupe_key=f"event-link:{ch['task_id']}:{ch['change']}:{ch.get('now', ch['was'])}",
                                    now_local=now_local, text=text)
        db.commit()

        today = now_local.date()
        busy = busy_intervals(self.calendar_source, today, self.tz)
        if busy is None:
            return
        workday = (parse_hhmm(self.settings.workday_start), parse_hhmm(self.settings.workday_end))
        moved = refit_today(db, get_plan(db, today), busy, now_local, self.tz, workday)
        if moved:
            lines = [f"{m['title']}: {m['now']}" if m["now"] else f"{m['title']}: no free time left today" for m in moved]
            report["replanned"] = len(moved)
            self.notifications.send(
                db, kind="nudge", priority="normal", now_local=now_local,
                dedupe_key=f"replan:{today}:" + ",".join(f"{m['title']}={m['now']}" for m in moved),
                text="A meeting now overlaps your plan, so the rest of today moved: " + "; ".join(lines) + ".",
            )
        db.commit()

    def _block(self, report: dict, reason: str | None) -> None:
        if reason:
            report["blocked"][reason] = report["blocked"].get(reason, 0) + 1

    def _reminders(self, db: Session, now_local: datetime, report: dict) -> None:
        now_utc = local_to_naive_utc(now_local, self.tz)
        for r in db.scalars(select(Reminder).where(Reminder.status == "pending", Reminder.fire_at <= now_utc).order_by(Reminder.fire_at)):
            if now_utc - r.fire_at > REMINDER_MAX_LATE:
                r.status = "failed"  # far too late to be useful; don't deliver a stale reminder
                db.commit()
                continue
            res = self.notifications.send(db, kind="reminder", text=f"Reminder: {r.text}", priority="high", dedupe_key=f"reminder:{r.id}", now_local=now_local)
            if res.sent:
                r.status, r.sent_at = "sent", now_utc
                db.commit()
                report["reminders_sent"] += 1
            elif res.reason == "duplicate":
                r.status, r.sent_at = "sent", now_utc  # already delivered earlier; just reconcile the row
                db.commit()
            else:
                self._block(report, res.reason)

    def _checkins(self, db: Session, now_local: datetime, report: dict) -> None:
        today = now_local.date()
        for kind in checkins_due(db, now_local, self.settings):
            facts = build_facts(db, kind, today, self.tz)
            if kind == "morning":
                workday = (parse_hhmm(self.settings.workday_start), parse_hhmm(self.settings.workday_end))
                facts["briefing"]["calendar_warnings"] = calendar_warnings(db, self.calendar_source, today, self.tz, workday)
            # Calendar titles come from other people: the model never sees them; code adds them, links removed.
            calendar = calendar_lines(calendar_events(self.calendar_source, today, self.tz)) if kind == "morning" else ""

            def word(k=kind, f=facts, cal=calendar) -> str:
                text = self.agent.compose_message(k, f, fallback_text(f))
                return f"{text}\n\n{cal}" if cal else text

            res = self.notifications.send(
                db, kind=kind, priority="normal", dedupe_key=f"checkin:{kind}:{today}", now_local=now_local, text=word,
            )
            if res.sent and res.text:
                db.add(Checkin(kind=kind, checkin_date=today, summary=res.text))
                db.commit()
                report["checkins_sent"].append(kind)
            else:
                self._block(report, res.reason)

    def _nudges(self, db: Session, now_local: datetime, report: dict) -> None:
        for n in detect_nudges(db, now_local, self.tz):
            res = self.notifications.send(
                db, kind="nudge", priority=n.priority, dedupe_key=n.dedupe_key, now_local=now_local,
                text=lambda n=n: self.agent.compose_message("nudge", n.facts, n.fallback),
            )
            if res.sent:
                report["nudges_sent"] += 1
                break  # at most one nudge per tick: no bursts
            if res.reason != "duplicate":
                self._block(report, res.reason)

    # ------------------------------------------------------------------ inbound chat
    def handle_inbound(self, text: str) -> str:
        """A message typed into Telegram: same agent, same latest conversation as the CLI/dashboard."""
        if text.startswith("/start") or text.startswith("/help"):
            return "Hi! Just message me like you would in the app. High-impact actions (deletes) must be approved in the dashboard or CLI."
        with self.session_factory() as db:
            today = datetime.now(self.tz).date()
            open_checkin = db.scalars(
                select(Checkin).where(Checkin.checkin_date == today, Checkin.user_response.is_(None)).order_by(Checkin.id.desc())
            ).first()
            if open_checkin and utcnow() - open_checkin.created_at < RESPONSE_WINDOW:
                open_checkin.user_response = text[:2000]
                db.commit()
            res = self.agent.chat(db, text, latest_conversation_id(db))
        reply = res.reply
        if res.pending_action_ids:
            reply += f"\n\n({len(res.pending_action_ids)} action(s) are waiting for your approval — approve them in the dashboard or CLI.)"
        return reply

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        from apscheduler.schedulers.background import BackgroundScheduler

        if self._aps is not None:
            return
        self._aps = BackgroundScheduler(timezone=str(self.tz))
        self._aps.add_job(self._safe_tick, "interval", seconds=self.settings.scheduler_tick_seconds, max_instances=1, coalesce=True, next_run_time=datetime.now(self.tz))
        self._aps.start()
        if self.settings.telegram_inbound and isinstance(self.notifier, TelegramNotifier):
            self._stop.clear()
            self._thread = threading.Thread(target=self._poll_loop, name="telegram-inbox", daemon=True)
            self._thread.start()
        logger.info("Scheduler started (tick every %ss)", self.settings.scheduler_tick_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._aps is not None:
            self._aps.shutdown(wait=False)
            self._aps = None

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:  # noqa: BLE001
            logger.exception("Scheduler tick crashed")

    def _poll_loop(self) -> None:
        inbox = TelegramInbox(self.notifier, self.handle_inbound)  # type: ignore[arg-type]
        while not self._stop.is_set():
            try:
                inbox.poll_once(timeout=25)
            except Exception:  # noqa: BLE001 - network blips: back off and keep going
                logger.warning("Telegram poll failed; retrying in 5s")
                self._stop.wait(5)
