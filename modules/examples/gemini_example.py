"""Gemini-style модуль для Max-Userbot (упрощённый порт).

Демонстрирует:
- Конфиг с несколькими валидаторами (`String`, `Hidden`, `Float`).
- Async client_ready.
- Команды с аргументами и автоматический edit ответа.
- Использование `httpx.AsyncClient` для внешнего API (если api_key задан).

Без api_key модуль всё равно загружается — `.g <вопрос>` просто показывает
заглушку. Это удобно для тестирования формы команды без реальных вызовов.

Внимание: это упрощённый порт — никаких медиа-вложений, истории чата по chat_id
и т. п. Эти фичи легко добавить, используя те же `self.get`/`self.set`.
"""

from __future__ import annotations

from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover - optional dep
    httpx = None  # type: ignore[assignment]

from core import loader, utils

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


@loader.tds
class GeminiExample(loader.Module):
    """Минимальный клиент Google Gemini API (пример нового API)."""

    strings = {
        "name": "GeminiExample",
        "no_api_key": (
            "❗️ <b>API-ключ не задан.</b>\n"
            "Установите его командой: <code>.cfg geminiexample api_key</code>"
        ),
        "no_prompt": "⚠️ <i>Нужен текст или ответ на сообщение с текстом.</i>",
        "thinking": "⌛️ <i>Думаю...</i>",
        "answer": "✨ <b>Gemini:</b>\n{text}",
        "http_unavailable": (
            "❗️ <b>Библиотека httpx не установлена.</b>\n"
            "Запустите: <code>pip install httpx</code>"
        ),
        "api_error": "❗️ <b>Ошибка API:</b> <code>{err}</code>",
    }

    def __init__(self) -> None:
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "api_key", "",
                "API-ключ Google Gemini.",
                validator=loader.validators.Hidden(),
            ),
            loader.ConfigValue(
                "model", "gemini-1.5-flash",
                "Имя модели Gemini.",
                validator=loader.validators.String(min_len=1, max_len=64),
            ),
            loader.ConfigValue(
                "temperature", 1.0,
                "Температура генерации (0..2).",
                validator=loader.validators.Float(minimum=0.0, maximum=2.0),
            ),
            loader.ConfigValue(
                "system_prompt", "",
                "Системный промпт (опционально).",
                validator=loader.validators.String(),
            ),
        )

    async def client_ready(self, client: Any, db: Any) -> None:
        self.client = client
        self.db = db

    @loader.command(
        ru_doc="<запрос> - спросить у Gemini",
        en_doc="<prompt> - ask Gemini",
        aliases=["gem"],
    )
    async def g(self, message) -> None:
        prompt = utils.get_args_raw(message)
        if not prompt:
            await utils.answer(message, self.strings["no_prompt"])
            return
        if httpx is None:
            await utils.answer(message, self.strings["http_unavailable"])
            return

        api_key = self.config["api_key"]
        if not api_key:
            await utils.answer(message, self.strings["no_api_key"])
            return

        await utils.answer(message, self.strings["thinking"])

        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": float(self.config["temperature"])},
        }
        sys_prompt = self.config["system_prompt"]
        if sys_prompt:
            body["systemInstruction"] = {"parts": [{"text": sys_prompt}]}

        url = GEMINI_ENDPOINT.format(model=self.config["model"])

        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                resp = await http.post(
                    url,
                    params={"key": api_key},
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            await utils.answer(
                message,
                self.strings["api_error"].format(err=utils.escape_html(str(exc))),
            )
            return

        text = self._extract_text(data) or "(пустой ответ)"
        await utils.answer(message, self.strings["answer"].format(text=utils.escape_html(text)))

    @loader.command(ru_doc="Сменить модель на следующую в списке: 1.5-flash → 1.5-pro → 2.0-flash")
    async def gmodel(self, message) -> None:
        rotation = ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"]
        current = self.config["model"]
        idx = rotation.index(current) if current in rotation else -1
        nxt = rotation[(idx + 1) % len(rotation)]
        self.config["model"] = nxt
        await utils.answer(message, f"🔁 Модель: <code>{nxt}</code>")

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        try:
            cand = payload["candidates"][0]
            parts = cand["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()
        except (KeyError, IndexError, TypeError):
            return ""
