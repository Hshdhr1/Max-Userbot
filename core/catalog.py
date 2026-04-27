"""Каталог модулей: загрузка списка, скачивание и установка.

Формат `catalog.json`:
    {
      "version": 1,
      "source": "Max-Userbot built-in",
      "modules": [
        {
          "name": "GeminiExample",
          "description": "Минимальный клиент Gemini API.",
          "version": "1.0",
          "author": "Max-Userbot",
          "url": "https://raw.githubusercontent.com/.../gemini_example.py",
          "filename": "gemini_example.py",
          "tags": ["ai"]
        },
        ...
      ]
    }

Поведение:
- `load_catalog(local_path)` сначала пробует `MAX_CATALOG_URL` (если задан и
  валиден), потом локальный файл.
- `install_module(entry, modules_dir, fetcher=None)` — скачивает по URL и
  пишет в `modules/<filename>`. Если файл уже есть и его sha256 совпадает —
  возвращает status="up_to_date".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger("max-userbot.catalog")

DEFAULT_CATALOG_PATH = Path("catalog.json")
MAX_DOWNLOAD_BYTES = 1_000_000  # 1 MB на один модуль — для здравого смысла
SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_\-]+\.py$")


@dataclass
class CatalogEntry:
    name: str
    description: str = ""
    version: str = "1.0"
    author: str = ""
    url: str = ""
    filename: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "url": self.url,
            "filename": self.filename,
            "tags": list(self.tags),
        }


@dataclass
class Catalog:
    version: int = 1
    source: str = ""
    modules: list[CatalogEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "source": self.source,
            "modules": [m.to_dict() for m in self.modules],
        }


def _parse_catalog(raw: dict) -> Catalog:
    if not isinstance(raw, dict):
        raise ValueError("catalog must be a JSON object")
    entries = []
    for item in raw.get("modules") or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        entries.append(
            CatalogEntry(
                name=name,
                description=(item.get("description") or "").strip(),
                version=(item.get("version") or "1.0").strip(),
                author=(item.get("author") or "").strip(),
                url=(item.get("url") or "").strip(),
                filename=(item.get("filename") or "").strip(),
                tags=[t for t in (item.get("tags") or []) if isinstance(t, str)],
            )
        )
    return Catalog(
        version=int(raw.get("version") or 1),
        source=str(raw.get("source") or ""),
        modules=entries,
    )


def load_catalog(local_path: Path = DEFAULT_CATALOG_PATH) -> Catalog:
    """Загрузить каталог. Приоритет:
    1) `MAX_CATALOG_URL` (env) — если получится скачать;
    2) локальный файл `catalog.json`;
    3) пустой каталог.
    """
    remote_url = (os.getenv("MAX_CATALOG_URL") or "").strip()
    if remote_url:
        try:
            data = _http_fetch_text(remote_url)
            return _parse_catalog(json.loads(data))
        except (urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Не удалось загрузить remote-каталог %s: %s", remote_url, exc)

    if local_path.exists():
        try:
            with local_path.open("r", encoding="utf-8") as fh:
                return _parse_catalog(json.load(fh))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Не удалось распарсить %s: %s", local_path, exc)

    return Catalog()


# ----------------------------- installation ----------------------------------


@dataclass
class InstallResult:
    status: str  # "installed" | "up_to_date" | "error"
    path: Path | None = None
    bytes_written: int = 0
    error: str = ""


def installed_filenames(modules_dir: Path) -> set[str]:
    if not modules_dir.is_dir():
        return set()
    return {p.name for p in modules_dir.glob("*.py") if not p.name.startswith("_")}


def annotate_installed(catalog: Catalog, modules_dir: Path) -> list[dict]:
    """Вернуть список словарей с дополнительным полем `installed`."""
    installed = installed_filenames(modules_dir)
    out = []
    for entry in catalog.modules:
        d = entry.to_dict()
        d["installed"] = bool(entry.filename and entry.filename in installed)
        out.append(d)
    return out


def install_module(
    entry: CatalogEntry,
    modules_dir: Path,
    fetcher: Callable[[str], bytes] | None = None,
) -> InstallResult:
    """Скачать модуль из `entry.url` и положить в `modules_dir/entry.filename`."""
    if not entry.url:
        return InstallResult(status="error", error="entry has no url")
    if not entry.filename or not SAFE_FILENAME.match(entry.filename):
        return InstallResult(
            status="error",
            error="filename должен быть простым ASCII-именем .py файла",
        )
    fetch = fetcher or _http_fetch_bytes
    try:
        payload = fetch(entry.url)
    except Exception as exc:  # noqa: BLE001
        return InstallResult(status="error", error=f"download failed: {exc}")

    if not payload:
        return InstallResult(status="error", error="empty payload")
    if len(payload) > MAX_DOWNLOAD_BYTES:
        return InstallResult(
            status="error",
            error=f"file too big: {len(payload)} > {MAX_DOWNLOAD_BYTES}",
        )

    target = modules_dir / entry.filename
    if target.exists():
        existing = target.read_bytes()
        if hashlib.sha256(existing).digest() == hashlib.sha256(payload).digest():
            return InstallResult(status="up_to_date", path=target, bytes_written=0)

    modules_dir.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(target)
    return InstallResult(status="installed", path=target, bytes_written=len(payload))


def uninstall_module(filename: str, modules_dir: Path) -> InstallResult:
    if not filename or not SAFE_FILENAME.match(filename):
        return InstallResult(status="error", error="invalid filename")
    target = modules_dir / filename
    if not target.exists():
        return InstallResult(status="error", error="not installed")
    target.unlink()
    return InstallResult(status="installed", path=target, bytes_written=0)


# ----------------------------- HTTP helpers ----------------------------------


def _http_fetch_bytes(url: str, timeout: float = 15.0) -> bytes:
    if not url.startswith(("http://", "https://")):
        raise ValueError("url must use http(s)")
    req = urllib.request.Request(url, headers={"User-Agent": "Max-Userbot/catalog"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL валидируется
        # читаем не больше MAX_DOWNLOAD_BYTES + 1, чтобы пораньше отказать
        return resp.read(MAX_DOWNLOAD_BYTES + 1)


def _http_fetch_text(url: str, timeout: float = 10.0) -> str:
    return _http_fetch_bytes(url, timeout=timeout).decode("utf-8")


def iter_safe_filenames(entries: Iterable[CatalogEntry]) -> set[str]:
    return {e.filename for e in entries if e.filename and SAFE_FILENAME.match(e.filename)}


__all__ = [
    "Catalog",
    "CatalogEntry",
    "InstallResult",
    "DEFAULT_CATALOG_PATH",
    "load_catalog",
    "install_module",
    "uninstall_module",
    "annotate_installed",
    "installed_filenames",
    "SAFE_FILENAME",
]
