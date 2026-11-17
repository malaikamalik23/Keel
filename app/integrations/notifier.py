"""Outbound delivery channels. A channel only delivers text; *whether* to send is decided by NotificationService."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)
TELEGRAM_LIMIT = 4096


class NotificationError(Exception):
    pass


class Notifier(Protocol):
    channel: str

    def send(self, text: str) -> None: ...


class LogNotifier:
    """Default when no channel is configured: messages are logged (and stored in `notifications`), not delivered."""

    channel = "log"

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)
        logger.info("NOTIFICATION: %s", text)


class TelegramNotifier:
    channel = "telegram"

    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        self._token = token
        self.chat_id = str(chat_id)
        self._client = client or httpx.Client(timeout=15)

    def api(self, method: str, payload: dict | None = None, timeout: float = 15) -> dict:
        """Call the Bot API. Errors never include the URL, because the URL contains the bot token."""
        try:
            resp = self._client.post(f"https://api.telegram.org/bot{self._token}/{method}", json=payload or {}, timeout=timeout)
        except httpx.HTTPError as exc:
            raise NotificationError(f"Telegram request failed ({type(exc).__name__})") from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or not data.get("ok"):
            raise NotificationError(f"Telegram rejected the request (HTTP {resp.status_code}): {str(data.get('description', ''))[:120]}")
        return data

    def send(self, text: str) -> None:
        self.api("sendMessage", {"chat_id": self.chat_id, "text": text[:TELEGRAM_LIMIT]})


class EmailNotifier:
    """Sends each message as a plain-text email to ONE fixed address (yours), over an encrypted connection only.

    The recipient comes from configuration, never from the model or the message, so this cannot be turned into a way
    to email anyone else. Errors never include the password or the server's reply (which can echo credentials).
    """

    channel = "email"

    def __init__(self, host: str, port: int, security: str, username: str | None, password: str | None,
                 sender: str, recipient: str, smtp_factory=None, timeout: float = 20):
        if security not in ("starttls", "ssl"):
            raise ValueError("SMTP_SECURITY must be 'starttls' or 'ssl'; unencrypted SMTP is not supported.")
        self.host, self.port, self.security = host, port, security
        self._username, self._password = username, password
        self.sender, self.recipient = sender, recipient
        self._timeout = timeout
        self._factory = smtp_factory  # tests pass a fake; None = the real smtplib classes

    @staticmethod
    def subject(text: str) -> str:
        first = next((line.strip() for line in text.splitlines() if line.strip()), "Message")
        first = " ".join(first.split())  # no newlines or control whitespace in a header
        return "Keel: " + (first if len(first) <= 70 else first[:67].rstrip() + "...")

    def build(self, text: str) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = self.subject(text)
        msg["From"] = self.sender
        msg["To"] = self.recipient
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="keel.local")
        msg["Auto-Submitted"] = "auto-generated"  # tells mail servers not to send auto-replies back
        msg.set_content(text + "\n\n--\nSent by your Keel agent. Reply in Keel, not to this email.\n")
        return msg

    def verify(self) -> None:
        """Connect, encrypt and log in, then hang up without sending anything (for scripts.smoke_integrations)."""
        self._session(None)

    def send(self, text: str) -> None:
        self._session(self.build(text))

    def _session(self, msg: EmailMessage | None) -> None:
        context = ssl.create_default_context()
        try:
            if self.security == "ssl":
                factory = self._factory or smtplib.SMTP_SSL
                server = factory(self.host, self.port, timeout=self._timeout, context=context)
            else:
                factory = self._factory or smtplib.SMTP
                server = factory(self.host, self.port, timeout=self._timeout)
            with server:
                if self.security == "starttls":
                    server.starttls(context=context)  # before login: the password never crosses in clear text
                if self._username:
                    server.login(self._username, self._password or "")
                if msg is not None:
                    server.send_message(msg, from_addr=self.sender, to_addrs=[self.recipient])
        except smtplib.SMTPAuthenticationError:
            raise NotificationError("The mail server rejected the username or password (for Gmail, use an App Password).") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise NotificationError(f"Email could not be sent ({type(exc).__name__}).") from None


def build_notifier(settings: Settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    if settings.smtp_host and settings.email_to:
        sender = settings.email_from or settings.smtp_username or settings.email_to
        return EmailNotifier(settings.smtp_host, settings.smtp_port, settings.smtp_security, settings.smtp_username,
                             settings.smtp_password, sender, settings.email_to)
    return LogNotifier()
