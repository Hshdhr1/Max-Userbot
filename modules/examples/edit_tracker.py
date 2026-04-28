"""EditTracker — ловит редактирование и удаление чужих сообщений.

Основан на vkmax-протоколе (см. `docs/MAX_PROTOCOL.md`):
opcode `128` приходит при любом изменении сообщения. Поле
`payload.message.status` принимает значения `"EDITED"` или `"REMOVED"`,
когда собеседник изменил/удалил сообщение.

Команды:
    .edits     — последние отредактированные сообщения в этом чате
    .deletes   — последние удалённые сообщения в этом чате
    .ettop     — статистика: сколько правок/удалений по каждому чату
    .etclear   — очистить историю по этому чату

Хранилище — `core.db.KeyValueDB` (JSON в `userbot_db.json`), namespace
"EditTracker". Никаких raw-SQL и SQL-injection — в отличие от примера
из `main.py`, где вся история строилась через f-string + sqlite3.

Конфиг:
    max_per_chat — сколько событий хранить на чат (default 50)
    track_outgoing — следить ли за своими сообщениями (default False)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core import loader, utils

logger = logging.getLogger("max-userbot.modules.edit_tracker")

NAMESPACE = "EditTracker"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _key(chat_id: int, kind: str) -> str:
    return f"chat:{chat_id}:{kind}"


@loader.tds
class EditTracker(loader.Module):
    """Логирует редактирования/удаления сообщений по opcode 128."""

    strings = {"name": "EditTracker"}

    def __init__(self) -> None:
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "max_per_chat",
                50,
                "Максимум событий на чат (старые вытесняются).",
                validator=loader.validators.Integer(minimum=1, maximum=10000),
            ),
            loader.ConfigValue(
                "track_outgoing",
                False,
                "Следить ли за собственными сообщениями.",
                validator=loader.validators.Boolean(),
            ),
        )
        self.db: Any = None

    async def client_ready(self, client, db) -> None:
        self.client = client
        self.db = db

    # ----- watcher: opcode 128 -------------------------------------------------

    @loader.watcher()
    async def watcher(self, message) -> None:
        # Используем сырой message-dict, чтобы достать поле `status`,
        # которое не выставляется на обычное входящее сообщение.
        try:
            msg = message.message or {}
        except Exception:
            return
        status = msg.get("status")
        if status not in {"EDITED", "REMOVED"}:
            return
        if message.is_outgoing and not bool(self.config["track_outgoing"]):
            return

        kind = "edited" if status == "EDITED" else "deleted"
        record = {
            "ts": _now_ms(),
            "id": int(msg.get("id") or 0),
            "sender": message.sender_id,
            "text": (msg.get("text") or "")[:2000],
            "prev_text": (msg.get("oldText") or msg.get("prevText") or "")[:2000],
        }
        self._append(message.chat_id, kind, record)

    # ----- store ---------------------------------------------------------------

    def _append(self, chat_id: int, kind: str, record: dict) -> None:
        if self.db is None:
            return
        existing = list(self.db.get(NAMESPACE, _key(chat_id, kind), []) or [])
        existing.append(record)
        cap = int(self.config["max_per_chat"])
        if len(existing) > cap:
            existing = existing[-cap:]
        self.db.set(NAMESPACE, _key(chat_id, kind), existing)

    def _read(self, chat_id: int, kind: str) -> list[dict]:
        if self.db is None:
            return []
        return list(self.db.get(NAMESPACE, _key(chat_id, kind), []) or [])

    # ----- commands ------------------------------------------------------------

    @loader.command(ru_doc="последние отредактированные сообщения в этом чате")
    async def edits(self, message) -> None:
        await self._render(message, "edited", "✏ Редактирования")

    @loader.command(ru_doc="последние удалённые сообщения в этом чате")
    async def deletes(self, message) -> None:
        await self._render(message, "deleted", "🗑 Удаления")

    async def _render(self, message, kind: str, title: str) -> None:
        events = self._read(message.chat_id, kind)
        if not events:
            await utils.answer(message, f"<b>{title}</b>\nПусто.")
            return
        lines = [f"<b>{title}</b> в этом чате (последние {len(events)}):", ""]
        for ev in reversed(events[-20:]):
            ts_h = time.strftime("%d.%m %H:%M:%S", time.localtime(ev["ts"] / 1000))
            sender = ev.get("sender") or "?"
            if kind == "edited":
                prev = utils.escape_html(ev.get("prev_text") or "(нет)")[:200]
                cur = utils.escape_html(ev.get("text") or "")[:200]
                lines.append(f"<i>{ts_h}</i> · {sender}\n  было: <code>{prev}</code>\n  стало: <code>{cur}</code>")
            else:
                txt = utils.escape_html(ev.get("text") or ev.get("prev_text") or "(пусто)")[:300]
                lines.append(f"<i>{ts_h}</i> · {sender}: <code>{txt}</code>")
        await utils.answer(message, "\n".join(lines))

    @loader.command(ru_doc="статистика: сколько правок/удалений по каждому чату")
    async def ettop(self, message) -> None:
        if self.db is None:
            await utils.answer(message, "DB недоступна")
            return
        # Собираем счётчики по всем chat:*:* ключам.
        ns = self.db._data.get(NAMESPACE, {}) if hasattr(self.db, "_data") else {}
        chats: dict[int, dict[str, int]] = {}
        for k, v in ns.items():
            # k = "chat:<id>:<kind>"
            parts = k.split(":")
            if len(parts) != 3 or parts[0] != "chat":
                continue
            try:
                cid = int(parts[1])
            except ValueError:
                continue
            kind = parts[2]
            chats.setdefault(cid, {"edited": 0, "deleted": 0})[kind] = len(v or [])
        if not chats:
            await utils.answer(message, "<b>EditTracker · top</b>\nПусто.")
            return
        rows = sorted(
            chats.items(),
            key=lambda kv: kv[1].get("edited", 0) + kv[1].get("deleted", 0),
            reverse=True,
        )[:20]
        lines = ["<b>EditTracker · топ чатов</b>", ""]
        for cid, c in rows:
            lines.append(f"<code>{cid}</code> — ✏ {c.get('edited', 0)} · 🗑 {c.get('deleted', 0)}")
        await utils.answer(message, "\n".join(lines))

    @loader.command(ru_doc="очистить историю по текущему чату")
    async def etclear(self, message) -> None:
        if self.db is None:
            await utils.answer(message, "DB недоступна")
            return
        for kind in ("edited", "deleted"):
            self.db.pop(NAMESPACE, _key(message.chat_id, kind), None)
        await utils.answer(message, "EditTracker: очищено для этого чата.")
