"""Логика рендера сайта в PDF и публикации файла наружу.

Модуль `modules/examples/site_dump.py` использует это ядро. Здесь — чистая
функциональная часть без зависимостей на MAX-протокол, удобно тестируется.

Стратегия рендера:
  1) `playwright` (если установлен) — настоящий headless Chromium, JS работает.
  2) `weasyprint` — без JS, но без системных зависимостей сложнее, всё ставится через pip.
  3) Если ничего нет — возвращаем понятную ошибку с инструкцией.

Стратегия публикации:
  1) `0x0.st` — анонимный, без ключа, возвращает plain-text URL.
  2) Локально сохраняем в `downloads/`. Если задан `MAX_PUBLIC_URL`, формируем
     URL `<base>/files/<token>/<name>.pdf` и админ должен поднять статику сам
     (или Web UI, если будет добавлен соответствующий раут).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import aiohttp

logger = logging.getLogger("max-userbot.site_dump")

DEFAULT_DOWNLOADS_DIR = Path("downloads")
MAX_PDF_SIZE_BYTES = 25 * 1024 * 1024  # 25 МБ — достаточно для большинства сайтов
DEFAULT_TIMEOUT = 30  # секунд на рендер


# ------------------------------ ошибки -------------------------------------


class SiteDumpError(RuntimeError):
    """Общее исключение модуля."""


class UrlValidationError(SiteDumpError):
    pass


class RendererUnavailableError(SiteDumpError):
    pass


# --------------------------- валидация URL ---------------------------------


def validate_url(url: str) -> str:
    """Проверяет URL. Возвращает нормализованный URL.

    Бросает `UrlValidationError` если:
      - схема не http/https,
      - хост — приватный IP (SSRF на 127.0.0.1, 10.x, 192.168.x, ...),
      - хост = `localhost`/пустая строка,
      - URL вообще не парсится.
    """
    if not url or not isinstance(url, str):
        raise UrlValidationError("URL пустой или не строка.")
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise UrlValidationError(f"Не удалось распарсить URL: {e}") from e

    if parsed.scheme not in {"http", "https"}:
        raise UrlValidationError(f"Схема {parsed.scheme!r} не поддерживается. Только http/https.")
    host = (parsed.hostname or "").lower()
    if not host:
        raise UrlValidationError("В URL нет хоста.")
    if host in {"localhost", "ip6-localhost", "broadcasthost"}:
        raise UrlValidationError("Локальные адреса заблокированы (SSRF-protection).")

    # Если уже IP — проверяем, что он не private/reserved.
    try:
        ip = ipaddress.ip_address(host)
        _ensure_public_ip(ip)
    except ValueError:
        # это hostname, а не IP — резолвим и проверяем все полученные адреса.
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError as e:
            raise UrlValidationError(f"Не удалось разрешить хост {host!r}: {e}") from e
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            _ensure_public_ip(ip)

    return url


def _ensure_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise UrlValidationError(
            f"IP {ip} приватный/служебный — заблокирован для SSRF-защиты."
        )


# --------------------------- безопасное имя --------------------------------


_SAFE_FILENAME_RX = re.compile(r"[^a-zA-Z0-9._-]")


def safe_filename_for(url: str) -> str:
    """Имя файла из URL: `https://example.com/foo` → `example.com-foo-<ts>.pdf`."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "site").replace(".", "_")
    path = parsed.path.strip("/").replace("/", "-")
    base = f"{host}-{path}" if path else host
    base = _SAFE_FILENAME_RX.sub("_", base)[:80]
    ts = int(time.time())
    return f"sdump-{base}-{ts}.pdf"


# ------------------------------- рендер ------------------------------------


@dataclass
class RenderOptions:
    timeout: int = DEFAULT_TIMEOUT
    viewport_width: int = 1280
    viewport_height: int = 800
    print_format: str = "A4"  # "A4" | "Letter"
    print_background: bool = True
    wait_until: str = "networkidle"  # "load"|"domcontentloaded"|"networkidle"


async def render_with_playwright(url: str, out_path: Path, opts: RenderOptions) -> Path:
    """Полноценный рендер через Chromium. Бросает RendererUnavailableError если playwright не установлен."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise RendererUnavailableError(
            "playwright не установлен. Поставьте: pip install playwright && playwright install chromium"
        ) from e

    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--no-sandbox"])
        try:
            context = await browser.new_context(
                viewport={"width": opts.viewport_width, "height": opts.viewport_height},
                user_agent="Mozilla/5.0 (Max-Userbot/sdump) Chrome/120 Safari/537.36",
            )
            page = await context.new_page()
            await page.goto(url, timeout=opts.timeout * 1000, wait_until=opts.wait_until)
            await page.pdf(
                path=str(out_path),
                format=opts.print_format,
                print_background=opts.print_background,
            )
        finally:
            await browser.close()
    return out_path


async def render_with_weasyprint(url: str, out_path: Path, opts: RenderOptions) -> Path:
    """Fallback без JS. Сначала аккуратно скачиваем HTML, потом гоним weasyprint."""
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError as e:
        raise RendererUnavailableError(
            "weasyprint не установлен. Поставьте: pip install weasyprint"
        ) from e

    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=opts.timeout)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            html_text = await resp.text(errors="replace")

    # weasyprint синхронный, выпихиваем в executor чтобы event loop не блочить.
    loop = asyncio.get_event_loop()

    def _run() -> None:
        HTML(string=html_text, base_url=url).write_pdf(str(out_path))

    await loop.run_in_executor(None, _run)
    return out_path


async def render_pdf(
    url: str,
    out_dir: Path | str = DEFAULT_DOWNLOADS_DIR,
    opts: RenderOptions | None = None,
    preferred: str = "auto",
) -> Path:
    """Рендерит URL в PDF, возвращает путь к файлу.

    `preferred`: "auto" → playwright → weasyprint; "playwright" / "weasyprint" — насильно.
    """
    url = validate_url(url)
    opts = opts or RenderOptions()
    out_dir = Path(out_dir)
    out_path = out_dir / safe_filename_for(url)

    errors: list[str] = []
    order = ["playwright", "weasyprint"] if preferred == "auto" else [preferred]
    for name in order:
        try:
            if name == "playwright":
                return await render_with_playwright(url, out_path, opts)
            if name == "weasyprint":
                return await render_with_weasyprint(url, out_path, opts)
            raise SiteDumpError(f"Неизвестный рендерер: {name!r}")
        except RendererUnavailableError as e:
            errors.append(f"{name}: {e}")
            continue
        except Exception as e:
            errors.append(f"{name}: {e!r}")
            logger.exception("render via %s failed", name)
            continue

    raise SiteDumpError(
        "Не удалось отрендерить PDF. Попытки:\n" + "\n".join(errors)
    )


# ----------------------------- публикация ----------------------------------


@dataclass
class UploadResult:
    url: str
    provider: str


async def upload_to_0x0(path: Path) -> UploadResult:
    """Анонимный upload на https://0x0.st (без ключей). Возвращает прямой URL."""
    if path.stat().st_size > MAX_PDF_SIZE_BYTES:
        raise SiteDumpError(f"Файл слишком большой: {path.stat().st_size} > {MAX_PDF_SIZE_BYTES} байт")
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        with open(path, "rb") as f:
            data = aiohttp.FormData()
            data.add_field("file", f, filename=path.name, content_type="application/pdf")
            async with session.post("https://0x0.st", data=data) as resp:
                resp.raise_for_status()
                url = (await resp.text()).strip()
    if not url.startswith("http"):
        raise SiteDumpError(f"0x0.st вернул неожиданный ответ: {url[:200]}")
    return UploadResult(url=url, provider="0x0.st")


async def publish_pdf(path: Path, provider: str = "auto") -> UploadResult:
    """Загружает PDF на публичный хост и возвращает URL.

    `provider`: "auto" / "0x0" / "none". При `none` — возвращает file:// URL,
    модуль скажет в чате, что файл сохранён локально.
    """
    if provider in {"none", "off", "disabled"}:
        return UploadResult(url=f"file://{path.resolve()}", provider="local")
    if provider in {"auto", "0x0", "0x0.st"}:
        return await upload_to_0x0(path)
    raise SiteDumpError(f"Неизвестный provider: {provider!r}")


# ----------------------------- multi-page crawl -----------------------------


_SKIP_EXTENSIONS: tuple[str, ...] = (
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".webm", ".mov", ".avi", ".wav", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


class _LinkExtractor(HTMLParser):
    """Очень маленький HTML-парсер на html.parser, без внешних зависимостей."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self.links.append(value)


def _normalize_link(base: str, raw: str) -> str | None:
    """Превращает href в абсолютный URL без фрагмента; возвращает None если линк не http(s)."""
    raw = (raw or "").strip()
    if not raw or raw.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    absolute = urljoin(base, raw)
    absolute, _frag = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return None
    if any(parsed.path.lower().endswith(ext) for ext in _SKIP_EXTENSIONS):
        return None
    return absolute


async def _fetch_html(session: aiohttp.ClientSession, url: str, timeout: int) -> str:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True) as resp:
        # Не пытаемся парсить бинарные ответы как HTML.
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "xml" not in ctype:
            return ""
        return await resp.text(errors="replace")


async def crawl_pages(
    start_url: str,
    max_pages: int = 30,
    max_depth: int = 2,
    same_domain_only: bool = True,
    timeout: int = 15,
) -> list[str]:
    """BFS по ссылкам, возвращает список URL'ов в порядке обхода (включая стартовый).

    Применяет ту же SSRF-валидацию, что и `validate_url` — для каждого URL,
    включая внутренние ссылки. Не уходит в субдомены если `same_domain_only`.
    Игнорирует расширения статики (CSS/JS/изображения/архивы).
    """
    start_url = validate_url(start_url)
    start_host = urlparse(start_url).hostname or ""
    seen: set[str] = {start_url}
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    out: list[str] = []

    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_obj) as session:
        while queue and len(out) < max_pages:
            url, depth = queue.popleft()
            out.append(url)
            if depth >= max_depth:
                continue
            try:
                html_text = await _fetch_html(session, url, timeout)
            except Exception as e:
                logger.warning("crawl: skip %s: %r", url, e)
                continue
            if not html_text:
                continue
            extractor = _LinkExtractor()
            try:
                extractor.feed(html_text)
            except Exception:
                continue
            for raw in extractor.links:
                link = _normalize_link(url, raw)
                if not link or link in seen:
                    continue
                if same_domain_only:
                    host = urlparse(link).hostname or ""
                    if host != start_host:
                        continue
                # SSRF-проверка для каждого нового URL.
                try:
                    validate_url(link)
                except UrlValidationError:
                    continue
                seen.add(link)
                queue.append((link, depth + 1))
                if len(seen) >= max_pages * 4:
                    # Бортик чтобы не разогнать seen в миллионах ссылок.
                    break
    return out


def merge_pdfs(parts: list[Path], output: Path) -> Path:
    """Склеивает несколько PDF'ов в один. Бросает RendererUnavailableError если нет pypdf."""
    try:
        from pypdf import PdfWriter  # type: ignore
    except ImportError as e:
        raise RendererUnavailableError(
            "pypdf не установлен. Поставьте: pip install pypdf"
        ) from e

    if not parts:
        raise SiteDumpError("merge_pdfs: список страниц пустой")

    output.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    try:
        for p in parts:
            if not p.exists() or p.stat().st_size == 0:
                continue
            writer.append(str(p))
        with open(output, "wb") as f:
            writer.write(f)
    finally:
        writer.close()
    return output


@dataclass
class FullSiteResult:
    pdf_path: Path
    pages: list[str] = field(default_factory=list)
    rendered: int = 0
    skipped: int = 0


async def render_full_site(
    start_url: str,
    out_dir: Path | str = DEFAULT_DOWNLOADS_DIR,
    opts: RenderOptions | None = None,
    renderer: str = "auto",
    max_pages: int = 30,
    max_depth: int = 2,
    same_domain_only: bool = True,
) -> FullSiteResult:
    """Краулит, рендерит каждую страницу в PDF, склеивает в один файл."""
    start_url = validate_url(start_url)
    opts = opts or RenderOptions()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pages = await crawl_pages(
        start_url,
        max_pages=max_pages,
        max_depth=max_depth,
        same_domain_only=same_domain_only,
        timeout=opts.timeout,
    )
    if not pages:
        raise SiteDumpError("Краулер не нашёл ни одной страницы.")

    parts_dir = out_dir / f"sdump-parts-{int(time.time())}"
    parts_dir.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    skipped = 0
    for page in pages:
        try:
            actual = await render_pdf(page, out_dir=parts_dir, opts=opts, preferred=renderer)
            if actual.exists() and actual.stat().st_size > 0:
                parts.append(actual)
            else:
                skipped += 1
        except SiteDumpError as e:
            logger.warning("render_full_site: skip %s: %s", page, e)
            skipped += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("render_full_site: skip %s: %r", page, e)
            skipped += 1

    if not parts:
        raise SiteDumpError("Не удалось отрендерить ни одной страницы.")

    merged = out_dir / safe_filename_for(start_url).replace("sdump-", "sdump-full-")
    merge_pdfs(parts, merged)

    # Чистим за собой временные части, оставляем только финальный PDF.
    for p in parts:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        parts_dir.rmdir()
    except OSError:
        pass

    return FullSiteResult(pdf_path=merged, pages=pages, rendered=len(parts), skipped=skipped)


# ------------------------------- pipeline ----------------------------------


@dataclass
class DumpResult:
    pdf_path: Path
    url: str
    provider: str
    bytes: int
    pages: int = 1


async def dump_url(
    url: str,
    out_dir: Path | str = DEFAULT_DOWNLOADS_DIR,
    opts: RenderOptions | None = None,
    renderer: str = "auto",
    upload: str = "auto",
    mode: str = "single",
    max_pages: int = 30,
    max_depth: int = 2,
    same_domain_only: bool = True,
) -> DumpResult:
    """Главный entry: single page или full site → PDF → publish."""
    if mode == "full":
        result = await render_full_site(
            url,
            out_dir=out_dir,
            opts=opts,
            renderer=renderer,
            max_pages=max_pages,
            max_depth=max_depth,
            same_domain_only=same_domain_only,
        )
        pub = await publish_pdf(result.pdf_path, provider=upload)
        return DumpResult(
            pdf_path=result.pdf_path,
            url=pub.url,
            provider=pub.provider,
            bytes=result.pdf_path.stat().st_size,
            pages=result.rendered,
        )
    pdf = await render_pdf(url, out_dir=out_dir, opts=opts, preferred=renderer)
    pub = await publish_pdf(pdf, provider=upload)
    return DumpResult(
        pdf_path=pdf,
        url=pub.url,
        provider=pub.provider,
        bytes=pdf.stat().st_size,
        pages=1,
    )
