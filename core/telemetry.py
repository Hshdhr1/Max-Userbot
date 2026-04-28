"""Анонимная opt-in телеметрия.

По умолчанию **выключена**. Если юзер включает (`telemetry_enabled=True`)
и задаёт `telemetry_endpoint` — бот раз в час шлёт компактный JSON со
счётчиками. Никаких `chat_id`, `sender_id`, текстов сообщений или имён
файлов в payload **не попадает** — это контролируется тестами.

Формат payload:
    {
        "anon_id": "<sha256 of install uuid>",
        "version": "max-userbot/<git-sha-or-version>",
        "uptime": 1234,                   # секунды
        "modules_count": 7,
        "commands_count": 42,
        "watchers_count": 3,
        "accounts": {"total": 1, "authorized": 1},
        "packets_in": 1500,
        "packets_out": 230,
        "commands_processed": 17,
        "top_commands": {"ping": 14, "weather": 3},
        "ts": 1700000000,
    }

`top_commands` — счётчики самих имён команд (а не аргументов). Чтобы это
работало, `userbot.py` инкрементит `Counter` через `record_command(cmd)`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("max-userbot.telemetry")


@dataclass
class TelemetryCounters:
    """Изолированный счётчик команд — без идентификации пользователя."""

    commands: Counter = field(default_factory=Counter)

    def record(self, command: str) -> None:
        if not command:
            return
        # Только имя команды, нижним регистром. Никаких аргументов.
        self.commands[command.lower()] += 1

    def snapshot(self, top_n: int = 20) -> dict[str, int]:
        return dict(self.commands.most_common(top_n))

    def reset(self) -> None:
        self.commands.clear()


def make_anon_id() -> str:
    """Генерит стабильный анонимный ID на установку.

    Берём случайный UUID4 → SHA256, чтобы наружу не утекал raw uuid (и его
    нельзя было использовать как идентификатор для cross-сервиса tracking'а).
    """
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


def build_payload(
    *,
    anon_id: str,
    version: str,
    uptime: int,
    modules_count: int,
    commands_count: int,
    watchers_count: int,
    accounts_total: int,
    accounts_authorized: int,
    packets_in: int,
    packets_out: int,
    commands_processed: int,
    top_commands: dict[str, int],
) -> dict[str, Any]:
    """Сборка payload. Отдельной функцией — чтобы тесты валидировали структуру."""
    return {
        "anon_id": anon_id,
        "version": version,
        "uptime": int(uptime),
        "modules_count": int(modules_count),
        "commands_count": int(commands_count),
        "watchers_count": int(watchers_count),
        "accounts": {"total": int(accounts_total), "authorized": int(accounts_authorized)},
        "packets_in": int(packets_in),
        "packets_out": int(packets_out),
        "commands_processed": int(commands_processed),
        "top_commands": dict(top_commands),
        "ts": int(time.time()),
    }


# Поля, которые НИКОГДА не должны оказаться в payload. Тест в
# `tests/test_telemetry.py` рекурсивно проверяет это.
_PII_FORBIDDEN_KEYS = frozenset({
    "chat_id",
    "sender_id",
    "user_id",
    "phone",
    "token",
    "password",
    "text",
    "message",
    "filename",
    "path",
    "ip",
})


def assert_no_pii(payload: Any) -> None:
    """Защита: выбрасывает ValueError, если в payload встретился запретный ключ."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if k in _PII_FORBIDDEN_KEYS:
                raise ValueError(f"PII-key {k!r} попало в telemetry payload")
            assert_no_pii(v)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            assert_no_pii(item)


async def send_payload(endpoint: str, payload: dict[str, Any], *, timeout: float = 10.0) -> bool:
    """POST'ит payload на endpoint. Возвращает True при 2xx."""
    try:
        import aiohttp  # ленивый импорт — телеметрия по умолчанию выключена.
    except ImportError:  # pragma: no cover
        logger.warning("aiohttp недоступен — телеметрия не отправлена")
        return False
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.post(endpoint, json=payload) as resp:
                if 200 <= resp.status < 300:
                    return True
                logger.warning("Telemetry POST → %s: HTTP %s", endpoint, resp.status)
                return False
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning("Telemetry POST не удался: %s", exc)
        return False


__all__ = [
    "TelemetryCounters",
    "make_anon_id",
    "build_payload",
    "assert_no_pii",
    "send_payload",
]
