"""Email delivery: encrypted only, one fixed recipient, nothing secret in errors, and it plugs into the normal budget."""
from __future__ import annotations

import smtplib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.integrations.notifier import EmailNotifier, LogNotifier, NotificationError, TelegramNotifier, build_notifier
from app.services.notifications import NotificationService
from tests.conftest import make_settings
from tests.fakes import ScriptedLLM

PASSWORD = "app-password-SECRET"


class FakeSMTP:
    """Records what the notifier did, in order, like a mail server would see it."""

    instances: list[FakeSMTP] = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.ssl_context = host, port, context
        self.calls: list[str] = []
        self.sent: list = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self, context=None):
        assert context is not None and context.verify_mode.name == "CERT_REQUIRED"  # certificates are checked
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append("send")
        self.sent.append((msg, from_addr, to_addrs))


@pytest.fixture(autouse=True)
def _reset():
    FakeSMTP.instances, FakeSMTP.fail_with = [], None


def _notifier(security="starttls", port=587):
    return EmailNotifier("smtp.example.com", port, security, "me@example.com", PASSWORD, "me@example.com", "me@example.com",
                         smtp_factory=FakeSMTP)


def test_starttls_happens_before_the_password_is_sent_and_the_mail_goes_only_to_the_configured_address():
    _notifier().send("Reminder: email Dr. Khan about the dataset\nIt was due today.")
    server = FakeSMTP.instances[0]
    assert server.calls == ["starttls", "login:me@example.com", "send", "quit"]
    msg, sender, to = server.sent[0]
    assert to == ["me@example.com"] and sender == "me@example.com"
    assert msg["Subject"] == "Keel: Reminder: email Dr. Khan about the dataset"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert "It was due today." in msg.get_content()
    assert msg.get_content_type() == "text/plain"  # no HTML: nothing that could load remote images or run


def test_ssl_mode_uses_an_encrypted_connection_from_the_start():
    _notifier("ssl", 465).send("Morning check-in")
    server = FakeSMTP.instances[0]
    assert server.ssl_context is not None and "starttls" not in server.calls


def test_unencrypted_smtp_is_refused():
    with pytest.raises(ValueError):
        EmailNotifier("smtp.example.com", 25, "none", "u", "p", "a@example.com", "a@example.com")
    with pytest.raises(Exception):  # noqa: B017 - pydantic refuses it at configuration time too
        Settings(_env_file=None, smtp_security="none")


@pytest.mark.parametrize("text", [
    "Subject line\r\nBcc: victim@example.com",           # header injection through the first line
    "First\nTo: someone-else@example.com\n\nbody",
    "\n\n   lots   of   space   \n",
    "x" * 500,
])
def test_the_message_cannot_add_headers_or_recipients(text):
    _notifier().send(text)
    msg, _, to = FakeSMTP.instances[0].sent[0]
    assert to == ["me@example.com"]
    assert msg.get_all("Bcc") is None and msg.get_all("To") == ["me@example.com"]
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"] and len(msg["Subject"]) <= 76


@pytest.mark.parametrize("error, expected", [
    (smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted " + PASSWORD.encode()), "App Password"),
    (smtplib.SMTPServerDisconnected("Connection unexpectedly closed: " + PASSWORD), "SMTPServerDisconnected"),
    (OSError("Network is unreachable"), "OSError"),
])
def test_errors_never_include_the_password_or_the_servers_reply(error, expected):
    FakeSMTP.fail_with = error
    with pytest.raises(NotificationError) as e:
        _notifier().send("hi")
    assert expected in str(e.value)
    assert PASSWORD not in str(e.value) and "535" not in str(e.value)
    assert e.value.__cause__ is None  # not chained either, so a traceback can't show it


def test_channel_choice_telegram_first_then_email_then_log():
    base = dict(smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password=PASSWORD, email_to="me@example.com")
    assert isinstance(build_notifier(make_settings(**base)), EmailNotifier)
    assert isinstance(build_notifier(make_settings(**base, telegram_bot_token="t", telegram_chat_id="42")), TelegramNotifier)
    assert isinstance(build_notifier(make_settings(smtp_host="smtp.example.com")), LogNotifier)  # no recipient: nothing is sent
    n = build_notifier(make_settings(**base))
    assert n.sender == "me@example.com" and n.recipient == "me@example.com"


def test_email_goes_through_the_same_budget_quiet_hours_and_dedupe_as_any_channel(make_container):
    c = make_container(ScriptedLLM([]), notification_max_per_day=1, checkin_reserve=0)
    service = NotificationService(_notifier(), c.settings)
    noon = datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo("UTC"))
    with c.session_factory() as db:
        first = service.send(db, kind="nudge", priority="high", text="You postponed the GRE set twice.", dedupe_key="n1", now_local=noon)
        again = service.send(db, kind="nudge", priority="high", text="You postponed the GRE set twice.", dedupe_key="n1", now_local=noon)
        over = service.send(db, kind="nudge", priority="high", text="Another nudge", dedupe_key="n2", now_local=noon)
    assert first.sent and not again.sent and not over.sent
    assert len(FakeSMTP.instances) == 1


def test_smoke_check_logs_in_without_sending_unless_asked(monkeypatch):
    from scripts import smoke_integrations as smoke

    monkeypatch.setattr("app.integrations.notifier.smtplib.SMTP", FakeSMTP)
    s = make_settings(smtp_host="smtp.example.com", smtp_username="me@example.com", smtp_password=PASSWORD, email_to="me@example.com")
    assert "nothing sent" in smoke.check_email(s)
    assert FakeSMTP.instances[0].calls == ["starttls", "login:me@example.com", "quit"]
    assert "sent one test email" in smoke.check_email(s, send=True)
    assert FakeSMTP.instances[1].sent[0][2] == ["me@example.com"]
    FakeSMTP.fail_with = smtplib.SMTPAuthenticationError(535, PASSWORD.encode())
    rows = smoke.run(s, checks=[("Email", smoke.check_email)])
    assert rows[0][1] == "FAIL" and PASSWORD not in rows[0][2]
