"""Inbound Telegram: chat with the agent from your phone.

Security: only messages you send in your private chat with the bot (the single configured chat id) are ever
processed -- everyone else, and every group, is ignored without a reply. Approving high-impact actions is deliberately NOT possible from here (use the dashboard or CLI).
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.integrations.notifier import NotificationError, TelegramNotifier

logger = logging.getLogger(__name__)


class TelegramInbox:
    def __init__(self, notifier: TelegramNotifier, handler: Callable[[str], str]):
        self.notifier = notifier
        self.handler = handler  # text in -> reply text out
        self._offset: int | None = None

    def _from_owner(self, msg: dict) -> bool:
        """Only a message you sent, in your private chat with the bot.

        The chat id alone is not enough: if TELEGRAM_CHAT_ID were a group, every member's message carries that id.
        In a private chat the chat id *is* your user id, so the sender must match it too and the chat must be private.
        """
        chat, sender = msg.get("chat") or {}, msg.get("from") or {}
        return (str(chat.get("id")) == self.notifier.chat_id and chat.get("type") == "private"
                and str(sender.get("id")) == self.notifier.chat_id)

    def poll_once(self, timeout: int = 0) -> int:
        """Fetch pending updates, answer the ones from our chat. Returns how many messages were handled."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        updates = self.notifier.api("getUpdates", payload, timeout=timeout + 10).get("result", [])
        handled = 0
        for u in updates:
            self._offset = max(self._offset or 0, int(u["update_id"]) + 1)  # acknowledge, even if ignored
            msg = u.get("message") or {}
            if not self._from_owner(msg):
                continue  # not you: ignore silently
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            try:
                reply = self.handler(text[:8000])
            except Exception:  # noqa: BLE001 - never let one bad message kill the poller
                logger.exception("Telegram handler failed")
                reply = "Sorry, something went wrong handling that."
            try:
                self.notifier.send(reply)
            except NotificationError:
                logger.warning("Could not send Telegram reply")
            handled += 1
        return handled
