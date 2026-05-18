"""Утилиты в стиле Hikka utils.* для Max-Userbot.

Большинство функций работает с `MaxMessage`, который мы строим из пакета MAX.
"""

from __future__ import annotations

import html
from typing import Any

from core.message import MaxMessage


def get_args_raw(message: MaxMessage) -> str:
    """Вернуть всё, что идёт после команды (без префикса и имени команды).

    `.cmd foo bar baz` → `foo bar baz`. Если префикс/команда не определяются —
    возвращаем весь текст.
    """
    text = (message.text or "").lstrip()
    if not text:
        return ""
    # Уберём префикс (любой из распространённых) и саму команду.
    body = text
    if body[0] in {".", "!", "/"}:
        body = body[1:]
    parts = body.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def get_args(message: MaxMessage) -> list[str]:
    """Аргументы команды, разбитые по пробелу (без кавычек, как str.split)."""
    raw = get_args_raw(message)
    return raw.split() if raw else []


def get_chat_id(message: MaxMessage) -> int:
    return message.chat_id


def get_message_id(message: MaxMessage) -> int:
    return message.id


def get_display_name(entity: Any) -> str:
    """Получить отображаемое имя пользователя или чата."""
    if not entity:
        return "Unknown"
    if hasattr(entity, "firstName"):
        first = getattr(entity, "firstName") or ""
        last = getattr(entity, "lastName") or ""
        return f"{first} {last}".strip() or "Unknown"
    return str(getattr(entity, "title", entity))


def escape_html(text: str) -> str:
    return html.escape(str(text), quote=False)


async def answer(message: MaxMessage, text: str, **kwargs: Any) -> MaxMessage:
    """Аналог Hikka `utils.answer` — редактирует своё сообщение текстом.

    `reply_markup` и прочее игнорируется — у Max нет полноценных инлайн-кнопок.
    """
    # В текущей версии API MAX через vkmax редактирование сообщения поддерживает только текст.
    await message.edit(text)
    return message


async def run_sync(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """Запуск синхронной функции в отдельном потоке."""
    import asyncio
    return await asyncio.to_thread(func, *args, **kwargs)


__all__ = [
    "answer",
    "escape_html",
    "get_args",
    "get_args_raw",
    "get_chat_id",
    "get_display_name",
    "get_message_id",
    "run_sync",
]
