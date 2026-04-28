"""SiteDump — `.sdump <url>` рендерит сайт в PDF и публикует результат.

Так как vkmax-протокол ещё не поддерживает upload файла как attachment,
модуль публикует PDF на анонимный хост (0x0.st по умолчанию) и отправляет
ссылку в тот же чат, откуда вызвана команда. Если provider = `none`, файл
сохраняется только локально в `downloads/` и URL отдаётся как `file://`.

Установка:
    pip install playwright pypdf
    playwright install chromium

Альтернатива (без JS):
    pip install weasyprint pypdf

Команды:
    .sdump <url>          — одна страница
    .sdump full <url>     — полный обход (все внутренние ссылки до max_depth)
    .sdump status         — какие рендереры/аплоадеры доступны

Конфиг (через .cfg SiteDump <key> <value>):
    renderer:    auto | playwright | weasyprint
    upload:      auto | 0x0 | none
    mode:        single | full   — режим .sdump <url> по умолчанию
    max_pages:   30              — верхняя граница страниц в full
    max_depth:   2               — глубина BFS в full
    timeout:     30
    wait_until:  networkidle | load | domcontentloaded
    print_format: A4 | Letter
"""

from __future__ import annotations

import logging
import time

from core import loader, utils
from core.site_dump import (
    DEFAULT_DOWNLOADS_DIR,
    RenderOptions,
    SiteDumpError,
    UrlValidationError,
    dump_url,
)

logger = logging.getLogger("max-userbot.modules.site_dump")


@loader.tds
class SiteDump(loader.Module):
    """Рендер любого сайта в PDF одной командой."""

    strings = {"name": "SiteDump"}

    def __init__(self) -> None:
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "renderer",
                "auto",
                "Какой рендерер использовать: auto / playwright / weasyprint",
                validator=loader.validators.Choice(["auto", "playwright", "weasyprint"]),
            ),
            loader.ConfigValue(
                "upload",
                "auto",
                "Куда выкладывать PDF: auto (0x0.st) / 0x0 / none (только локально)",
                validator=loader.validators.Choice(["auto", "0x0", "none"]),
            ),
            loader.ConfigValue(
                "timeout",
                30,
                "Таймаут на рендер (секунды).",
                validator=loader.validators.Integer(minimum=5, maximum=180),
            ),
            loader.ConfigValue(
                "wait_until",
                "networkidle",
                "Когда считать страницу готовой (для playwright).",
                validator=loader.validators.Choice(["load", "domcontentloaded", "networkidle"]),
            ),
            loader.ConfigValue(
                "print_format",
                "A4",
                "Формат страницы PDF.",
                validator=loader.validators.Choice(["A4", "Letter"]),
            ),
            loader.ConfigValue(
                "mode",
                "single",
                "Режим по умолчанию для .sdump <url>: single (одна страница) или full (полный обход).",
                validator=loader.validators.Choice(["single", "full"]),
            ),
            loader.ConfigValue(
                "max_pages",
                30,
                "Сколько максимум страниц обходить в режиме full.",
                validator=loader.validators.Integer(minimum=1, maximum=200),
            ),
            loader.ConfigValue(
                "max_depth",
                2,
                "Глубина BFS-обхода в режиме full.",
                validator=loader.validators.Integer(minimum=1, maximum=5),
            ),
            loader.ConfigValue(
                "same_domain_only",
                True,
                "Не уходить на внешние домены при обходе.",
                validator=loader.validators.Boolean(),
            ),
        )

    async def client_ready(self, client, db) -> None:
        self.client = client
        self.db = db

    @loader.command(
        ru_doc="<url> | full <url> | status — рендерит сайт в PDF",
        aliases=["pdf"],
    )
    async def sdump(self, message) -> None:
        arg = utils.get_args_raw(message).strip()
        if not arg:
            await utils.answer(
                message,
                "Использование:\n"
                "  <code>.sdump &lt;url&gt;</code> — одна страница\n"
                "  <code>.sdump full &lt;url&gt;</code> — полный обход\n"
                "  <code>.sdump status</code> — что установлено",
            )
            return

        if arg == "status":
            await utils.answer(message, await self._status_text())
            return

        # Парсим режим: "full <url>" → mode=full; иначе берём из конфига.
        mode = str(self.config["mode"])
        url = arg
        first, _, rest = arg.partition(" ")
        if first.lower() in {"full", "single"} and rest.strip():
            mode = first.lower()
            url = rest.strip()

        max_pages = int(self.config["max_pages"])
        max_depth = int(self.config["max_depth"])
        same_domain_only = bool(self.config["same_domain_only"])

        if mode == "full":
            await utils.answer(
                message,
                f"🌐 Полный обход: до {max_pages} страниц, глубина {max_depth}…",
            )
        else:
            await utils.answer(message, "🌐 Рендерю страницу…")

        opts = RenderOptions(
            timeout=int(self.config["timeout"]),
            wait_until=str(self.config["wait_until"]),
            print_format=str(self.config["print_format"]),
        )

        started = time.time()
        try:
            result = await dump_url(
                url,
                out_dir=DEFAULT_DOWNLOADS_DIR,
                opts=opts,
                renderer=str(self.config["renderer"]),
                upload=str(self.config["upload"]),
                mode=mode,
                max_pages=max_pages,
                max_depth=max_depth,
                same_domain_only=same_domain_only,
            )
        except UrlValidationError as e:
            await utils.answer(message, f"❌ Невалидный URL: {utils.escape_html(str(e))}")
            return
        except SiteDumpError as e:
            await utils.answer(
                message,
                "❌ Не удалось получить PDF.\n"
                f"<code>{utils.escape_html(str(e))[:1500]}</code>\n\n"
                "Подсказка: <code>pip install playwright pypdf && playwright install chromium</code>",
            )
            return
        except Exception as e:
            logger.exception("sdump failed")
            await utils.answer(message, f"❌ Ошибка: <code>{utils.escape_html(repr(e))[:500]}</code>")
            return

        elapsed = time.time() - started
        size_kb = result.bytes // 1024
        pages_line = (
            f"Страниц склеено: {result.pages}\n" if mode == "full" else ""
        )
        await utils.answer(
            message,
            (
                "📄 <b>PDF готов</b>\n"
                f"Источник: <code>{utils.escape_html(url)[:200]}</code>\n"
                f"Режим: <code>{mode}</code>\n"
                f"{pages_line}"
                f"Файл: <code>{utils.escape_html(result.pdf_path.name)}</code> ({size_kb} КБ)\n"
                f"Время: {elapsed:.1f} с\n"
                f"Ссылка ({utils.escape_html(result.provider)}): {utils.escape_html(result.url)}"
            ),
        )

    async def _status_text(self) -> str:
        try:
            import playwright  # noqa: F401

            pw = "✅ установлен"
        except ImportError:
            pw = "❌ не установлен — <code>pip install playwright && playwright install chromium</code>"
        try:
            import weasyprint  # noqa: F401

            wp = "✅ установлен"
        except ImportError:
            wp = "❌ не установлен — <code>pip install weasyprint</code>"
        try:
            import pypdf  # noqa: F401

            pp = "✅ установлен"
        except ImportError:
            pp = "❌ не установлен — <code>pip install pypdf</code> (нужен для full-режима)"
        return (
            "<b>SiteDump · статус</b>\n"
            f"playwright: {pw}\n"
            f"weasyprint: {wp}\n"
            f"pypdf: {pp}\n"
            f"renderer (config): <code>{utils.escape_html(str(self.config['renderer']))}</code>\n"
            f"upload (config): <code>{utils.escape_html(str(self.config['upload']))}</code>\n"
            f"mode (config): <code>{utils.escape_html(str(self.config['mode']))}</code>\n"
            f"max_pages: <code>{int(self.config['max_pages'])}</code>, "
            f"max_depth: <code>{int(self.config['max_depth'])}</code>"
        )
