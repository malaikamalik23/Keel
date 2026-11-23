"""Push notifications to the devices where you turned them on (the installed dashboard on your phone or laptop).

Standard Web Push: each message is encrypted for that one device (RFC 8291), so the browser vendor's push service that
relays it (Google, Apple, Mozilla, Microsoft) sees only that a message was sent, not what it says. Keel signs its
messages with its own key pair (VAPID), created on first use in data/ and never sent anywhere.

Keel only ever contacts the real push services: a subscription whose address isn't on one of their hosts is refused, so
a crafted "device" can't make Keel send requests somewhere else.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from sqlalchemy import select

from app.db.models import PushSubscription, utcnow
from app.integrations.notifier import NotificationError

logger = logging.getLogger(__name__)
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
MAX_FAILURES = 5
TTL_SECONDS = 12 * 3600  # a message for a phone that's off for longer than this is dropped, not delivered stale


class PushError(Exception):
    pass


def check_endpoint(endpoint: str) -> str:
    parts = urlsplit(endpoint or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS) or parts.username or parts.port not in (None, 443):
        raise PushError("That isn't a browser push address Keel accepts (Chrome, Firefox, Safari or Edge).")
    if len(endpoint) > 1000:
        raise PushError("That push address is too long.")
    return endpoint


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def check_keys(p256dh: str, auth: str) -> tuple[str, str]:
    try:
        key = base64.urlsafe_b64decode(p256dh + "=" * (-len(p256dh) % 4))
        secret = base64.urlsafe_b64decode(auth + "=" * (-len(auth) % 4))
    except (ValueError, TypeError):
        raise PushError("The device's push keys aren't valid.") from None
    if len(key) != 65 or key[0] != 4 or len(secret) != 16:
        raise PushError("The device's push keys aren't valid.")
    return p256dh, auth


def load_or_create_keys(path: Path):
    """Keel's VAPID key pair: created once, readable by you only."""
    from py_vapid import Vapid02

    if path.exists():
        return Vapid02.from_file(str(path))
    v = Vapid02()
    v.generate_keys()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(v.private_pem())
    return v


def public_key(path: Path) -> str:
    """The key a browser needs to subscribe (applicationServerKey), base64url."""
    v = load_or_create_keys(path)
    return _b64(v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))


class PushNotifier:
    """Sends one encrypted message to every subscribed device. Forgets devices that unsubscribed or keep failing."""

    channel = "push"

    def __init__(self, session_factory, key_path: Path, contact: str, sender=None, background: bool = True):
        self.session_factory = session_factory
        self.key_path = key_path
        self.contact = contact
        self._send = sender  # tests pass a fake; None = pywebpush
        self._background = background
        self._lock = threading.Lock()
        self._subs: list[dict] = []
        self.reload()

    def reload(self) -> None:
        """Read the device list. Called at startup and after a device is added or removed, never while a message is
        being sent: sending happens inside the scheduler's transaction and must not open the database itself."""
        with self.session_factory() as db:
            subs = [{"id": s.id, "endpoint": s.endpoint, "p256dh": s.p256dh, "auth": s.auth, "failures": s.failures or 0}
                    for s in db.scalars(select(PushSubscription))]
        with self._lock:
            self._subs = subs

    def devices(self) -> int:
        with self._lock:
            return len(self._subs)

    def send(self, text: str, *, title: str = "Keel", url: str = "/") -> int:
        """Returns how many devices got it (0 when none are subscribed). Raises only if every device failed."""
        with self._lock:
            subs = [dict(s) for s in self._subs]
        if not subs:
            return 0
        payload = json.dumps({"title": title, "body": text[:500], "url": url})
        vapid = load_or_create_keys(self.key_path)
        delivered, failed, outcomes = 0, [], []
        for s in subs:
            try:
                status = self._deliver({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, payload, vapid, s["endpoint"])
            except Exception as exc:  # noqa: BLE001 - one device failing mustn't stop the others
                status, failed = None, [*failed, type(exc).__name__]
            ok = status is not None and 200 <= status < 300
            delivered += ok
            if not ok and status not in (None, 404, 410):
                failed.append(f"HTTP {status}")
            outcomes.append((s["id"], status, ok))
        # Bookkeeping (last delivered, failures, forgetting dead devices) in its own transaction, after the caller's.
        if self._background:
            threading.Thread(target=self._record, args=(outcomes,), daemon=True).start()
        else:
            self._record(outcomes)
        if delivered == 0 and failed:
            raise NotificationError(f"Push delivery failed ({', '.join(sorted(set(failed)))}).")
        return delivered

    def _record(self, outcomes: list[tuple[int, int | None, bool]]) -> None:
        try:
            with self.session_factory() as db:
                for sid, status, ok in outcomes:
                    s = db.get(PushSubscription, sid)
                    if s is None:
                        continue
                    if status in (404, 410):  # the browser unsubscribed: forget it
                        db.delete(s)
                    elif ok:
                        s.last_ok_at, s.failures = utcnow(), 0
                    else:
                        s.failures = (s.failures or 0) + 1
                        if s.failures >= MAX_FAILURES:
                            db.delete(s)
                db.commit()
            self.reload()
        except Exception:  # noqa: BLE001 - bookkeeping must never break notifications
            logger.warning("Could not record push delivery results", exc_info=True)

    def _deliver(self, info: dict, payload: str, vapid, endpoint: str) -> int:
        check_endpoint(endpoint)  # checked again on every send, not just when subscribing
        if self._send is not None:
            return self._send(info, payload)
        from pywebpush import WebPushException, webpush

        parts = urlsplit(endpoint)
        try:
            resp = webpush(info, data=payload, vapid_private_key=vapid, timeout=15, ttl=TTL_SECONDS,
                           vapid_claims={"sub": self.contact, "aud": f"{parts.scheme}://{parts.netloc}"})
        except WebPushException as exc:
            return exc.response.status_code if exc.response is not None else 0
        return resp.status_code


class CombinedNotifier:
    """Email and push together. A message counts as delivered if any channel delivered it."""

    def __init__(self, *parts):
        self.parts = [p for p in parts if p is not None]
        self.channel = "+".join(p.channel for p in self.parts)

    def send(self, text: str) -> None:
        delivered, errors = False, []
        for p in self.parts:
            try:
                result = p.send(text)
                delivered = delivered or result is None or result > 0
            except NotificationError as exc:
                errors.append(str(exc))
        if not delivered and errors:
            raise NotificationError("; ".join(errors))
        if not delivered:
            logger.info("NOTIFICATION (no device or email set up): %s", text)
