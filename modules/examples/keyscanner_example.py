"""KeyScanner-style модуль для Max-Userbot (упрощённый порт).

Демонстрирует:
- Hikka-совместимый API (`@loader.command`, `@loader.watcher`, `loader.ConfigValue`).
- Использование per-module key/value DB через `self.get`/`self.set`.
- `self.config[...]` и валидаторы.
- `MaxMessage` — единый интерфейс для текста/чата/редактирования.

В отличие от оригинала, этот пример НЕ ходит по диалогам и не валидирует ключи
по сети — это убрано намеренно, чтобы пример работал без сторонних зависимостей.
Можно использовать как скелет для более полного порта.
"""

from __future__ import annotations

import re
from typing import Any

from core import loader, utils

KEY_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai":     re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "openrouter": re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b"),
    "anthropic":  re.compile(r"\bsk-ant-[A-Za-z0-9-]{20,}\b"),
    "google":     re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    "groq":       re.compile(r"\bgsk_[A-Za-z0-9]{30,}\b"),
    "huggingface": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
}

DB_KEYS = "ks_keys"
DB_AUTOCATCH = "ks_autocatch_chats"


@loader.tds
class KeyScannerExample(loader.Module):
    """Сохраняет AI API-ключи, замеченные в чатах. (пример нового API)"""

    strings = {
        "name": "KeyScannerExample",
        "auto_on": "🔔 <b>Авто-ловля включена</b> в этом чате.",
        "auto_off": "🔕 <b>Авто-ловля выключена</b> в этом чате.",
        "stats": "📦 <b>База:</b> {total} ключ(ей)\n{by_provider}",
        "no_keys": "📭 <b>В базе пока пусто.</b>",
        "cleared": "🧹 <b>База очищена.</b>",
        "found_one": "🆕 <b>Поймал ключ {provider}.</b> Всего: <code>{total}</code>",
    }

    def __init__(self) -> None:
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "watch_edits", True,
                "Реагировать ли на отредактированные сообщения.",
                validator=loader.validators.Boolean(),
            ),
            loader.ConfigValue(
                "min_key_length", 16,
                "Минимальная длина строки, чтобы считать её потенциальным ключом.",
                validator=loader.validators.Integer(minimum=4, maximum=512),
            ),
            loader.ConfigValue(
                "notify_on_new", True,
                "Отвечать в чат при поимке нового ключа.",
                validator=loader.validators.Boolean(),
            ),
        )

    # -------------------- lifecycle --------------------

    async def client_ready(self, client: Any, db: Any) -> None:
        self.client = client
        self.db = db

    # -------------------- helpers --------------------

    def _all_keys(self) -> dict[str, str]:
        keys = self.get(DB_KEYS, {})
        return keys if isinstance(keys, dict) else {}

    def _all_chats(self) -> list[int]:
        chats = self.get(DB_AUTOCATCH, [])
        return list(chats) if isinstance(chats, list) else []

    def _scan_text(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if not text:
            return out
        min_len = int(self.config["min_key_length"])
        for provider, pattern in KEY_PATTERNS.items():
            for match in pattern.findall(text):
                if len(match) >= min_len:
                    out.append((provider, match))
        return out

    # -------------------- commands --------------------

    @loader.command(ru_doc="Вкл/выкл авто-ловлю ключей в этом чате.")
    async def ksauto(self, message) -> None:
        chats = self._all_chats()
        chat_id = utils.get_chat_id(message)
        if chat_id in chats:
            chats.remove(chat_id)
            self.set(DB_AUTOCATCH, chats)
            await utils.answer(message, self.strings["auto_off"])
        else:
            chats.append(chat_id)
            self.set(DB_AUTOCATCH, chats)
            await utils.answer(message, self.strings["auto_on"])

    @loader.command(ru_doc="Показать статистику ключей в базе.", aliases=["ksstats"])
    async def ksstat(self, message) -> None:
        keys = self._all_keys()
        if not keys:
            await utils.answer(message, self.strings["no_keys"])
            return
        by_provider: dict[str, int] = {}
        for provider in keys.values():
            by_provider[provider] = by_provider.get(provider, 0) + 1
        lines = "\n".join(f"  • <b>{p}</b>: {n}" for p, n in sorted(by_provider.items()))
        await utils.answer(
            message,
            self.strings["stats"].format(total=len(keys), by_provider=lines),
        )

    @loader.command(ru_doc="Полностью очистить базу пойманных ключей.")
    async def ksclear(self, message) -> None:
        self.set(DB_KEYS, {})
        await utils.answer(message, self.strings["cleared"])

    @loader.command(ru_doc="Сканировать текущее сообщение на ключи (или текст после команды).")
    async def kscan(self, message) -> None:
        target_text = utils.get_args_raw(message) or message.text
        found = self._scan_text(target_text)
        if not found:
            await utils.answer(message, "🔎 Ключей не найдено.")
            return
        keys = self._all_keys()
        for provider, key in found:
            keys[key] = provider
        self.set(DB_KEYS, keys)
        await utils.answer(
            message,
            f"🆕 Сохранил <b>{len(found)}</b> новых ключ(ей). Всего в базе: <code>{len(keys)}</code>.",
        )

    # -------------------- watcher --------------------

    @loader.watcher(only_messages=True, only_incoming=False)
    async def autocatch(self, message) -> None:
        if not self.config["watch_edits"] and message.is_edited:
            return
        chats = self._all_chats()
        if utils.get_chat_id(message) not in chats:
            return
        found = self._scan_text(message.text)
        if not found:
            return
        keys = self._all_keys()
        new_count = 0
        for provider, key in found:
            if key not in keys:
                keys[key] = provider
                new_count += 1
        if not new_count:
            return
        self.set(DB_KEYS, keys)
        if self.config["notify_on_new"]:
            await message.reply(
                self.strings["found_one"].format(
                    provider=found[0][0], total=len(keys)
                )
            )
