"""Smoke-test the live integrations with YOUR keys: one read-only call each. Never sends anything, never writes.

    python -m scripts.smoke_integrations
    python -m scripts.smoke_integrations --send-test-email     # also email ONE test message to EMAIL_TO (yourself)

For each integration: if its setting is missing -> SKIPPED(no key); otherwise one read-only request -> PASS / FAIL.
Telegram is only checked with getMe (validates the bot token); no message is ever sent. Email is checked by connecting,
encrypting and logging in, then hanging up; a test email is sent only with --send-test-email, and only to EMAIL_TO. Notes are only listed on disk
(nothing is indexed into the database). Key values are never printed: every message is also scrubbed of them.
Exit code is 1 if anything FAILED, else 0.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from app.config import Settings
from app.services.connections import (  # noqa: F401 - re-exported for callers and tests
    Skip,
    check_brave,
    check_calendar,
    check_email,
    check_github,
    check_notes,
    check_telegram,
    scrub,
)

Check = Callable[[Settings], str]  # returns a short detail on success; raises Skip if unconfigured, anything else = FAIL


CHECKS: list[tuple[str, Check]] = [
    ("Brave search", check_brave),
    ("GitHub", check_github),
    ("Notes folder", check_notes),
    ("Calendar", check_calendar),
    ("Telegram", check_telegram),
    ("Email", check_email),
]


def _scrub(message: str, s: Settings) -> str:
    return scrub(message, s)


def run(settings: Settings | None = None, checks: list[tuple[str, Check]] | None = None) -> list[tuple[str, str, str]]:
    s = settings or Settings()
    rows = []
    for name, check in checks or CHECKS:
        try:
            rows.append((name, "PASS", _scrub(check(s), s)))
        except Skip as why:
            rows.append((name, "SKIPPED(no key)", str(why)))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            rows.append((name, "FAIL", _scrub(f"{type(exc).__name__}: {exc}", s)))
    return rows


def main(argv: list[str] | None = None) -> int:
    send = "--send-test-email" in (sys.argv[1:] if argv is None else argv)
    checks = [(n, (lambda st: check_email(st, send=True)) if n == "Email" and send else c) for n, c in CHECKS]
    rows = run(checks=checks)
    width = max(len(n) for n, _, _ in rows)
    for name, status, detail in rows:
        print(f"{name:<{width}}  {status:<16} {detail}")
    return 1 if any(status == "FAIL" for _, status, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
