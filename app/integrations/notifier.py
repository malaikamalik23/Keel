"""Outbound delivery channels. A channel only delivers text; *whether* to send is decided by NotificationService."""
from __future__ import annotations

import logging
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


def build_notifier(settings: Settings) -> Notifier:
    if settings.telegram_bot_token and settings.telegram_chat_id:
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    return LogNotifier()
