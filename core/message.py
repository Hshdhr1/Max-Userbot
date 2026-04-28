"""Адаптер сообщения в стиле Telethon Message для Max-userbot.

Конкретные ключи Max-протокола (`opcode=128`, `payload.message`, и т.д.) скрыты
здесь, чтобы модули могли работать с привычным API:

    await message.edit("text")
    await message.reply("text")
    await message.answer("text")  # alias

Атрибуты:

- `.text`            — текст сообщения (str)
- `.id`              — id сообщения (int)
- `.chat_id`         — id чата (int)
- `.sender_id`       — id отправителя (int | None)
- `.is_outgoing`     — отправлено самим юзером
- `.is_edited`       — это редактирование
- `.raw`             — оригинальный пакет (dict) — на случай нестандартных полей
- `.client`          — низкоуровневый MaxClient (для send_file и т.п.)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("max-userbot.message")


class MaxMessage:
    """Лёгкая обёртка над пакетом MAX, ведущая себя похоже на Telethon Message."""

    def __init__(self, client: Any, packet: dict, registry: Any | None = None) -> None:
        self._client = client
        self._packet = packet or {}
        self._registry = registry
        payload = self._packet.get("payload", {}) or {}
        message = payload.get("message", {}) or {}

        self.payload = payload
        self.message = message
        self.opcode = self._packet.get("opcode")

        self.text: str = (message.get("text") or "").strip()
        self.id: int = int(message.get("id") or 0)
        self.chat_id: int = int(payload.get("chatId") or 0)
        sender = message.get("senderId") or message.get("sender") or message.get("authorId")
        self.sender_id: Optional[int] = int(sender) if sender is not None else None

        self.is_outgoing: bool = bool(payload.get("outgoing") or message.get("outgoing"))
        self.is_edited: bool = bool(payload.get("edited") or message.get("edited"))
        self.raw: dict = self._packet

    # ---- helpers -------------------------------------------------------------

    @property
    def client(self) -> Any:
        return self._client

    async def edit(self, text: str) -> None:
        """Отредактировать текущее сообщение (если возможно)."""
        from userbot import edit_message

        if not self.id or not self.chat_id:
            logger.warning("MaxMessage.edit: chat_id/message_id отсутствуют")
            return
        await edit_message(self._client, self.chat_id, self.id, text)

    async def reply(self, text: str) -> None:
        """Отправить новое сообщение в этот чат."""
        from userbot import send_message

        if not self.chat_id:
            logger.warning("MaxMessage.reply: chat_id отсутствует")
            return
        await send_message(self._client, self.chat_id, text)

    # `answer` — алиас для совместимости с Hikka utils.answer().
    async def answer(self, text: str) -> None:
        await self.edit(text)


__all__ = ["MaxMessage"]
