"""Простой key/value DB для модулей в стиле Hikka/Heroku.

Каждый модуль получает собственное "пространство имён" (по имени модуля),
внутри которого может хранить произвольные JSON-сериализуемые значения.
DB сохраняется в один файл (`userbot_db.json`) и сериализуется лениво —
после каждого `set`/`pop`. Для обычных нагрузок (десятки-сотни ключей) этого
хватает; если когда-нибудь понадобится — заменить на sqlite/aiofiles.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("max-userbot.db")

DEFAULT_DB_PATH = Path("userbot_db.json")


class KeyValueDB:
    """Двухуровневый dict[namespace][key] = value, сохраняется в JSON-файл."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    # --- internal helpers ----------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("DB %s unreadable, starting empty: %s", self.path, exc)
            return
        if not isinstance(payload, dict):
            logger.warning("DB %s root is not a dict, starting empty", self.path)
            return
        # Убедимся, что значения первого уровня тоже dict.
        cleaned: dict[str, dict[str, Any]] = {}
        for ns, value in payload.items():
            if isinstance(value, dict):
                cleaned[str(ns)] = dict(value)
        self._data = cleaned

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Failed to persist DB %s: %s", self.path, exc)

    # --- Hikka-style API -----------------------------------------------------

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Прочитать значение из пространства имён `namespace`."""
        with self._lock:
            return self._data.get(namespace, {}).get(key, default)

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Записать значение в пространство имён."""
        with self._lock:
            self._data.setdefault(namespace, {})[key] = value
            self._save()

    def pop(self, namespace: str, key: str, default: Any = None) -> Any:
        """Удалить ключ из пространства имён, вернув значение."""
        with self._lock:
            ns = self._data.get(namespace)
            if ns is None or key not in ns:
                return default
            value = ns.pop(key)
            if not ns:
                self._data.pop(namespace, None)
            self._save()
            return value

    def all(self, namespace: str) -> dict[str, Any]:
        """Все ключи пространства имён (копия)."""
        with self._lock:
            return dict(self._data.get(namespace, {}))

    def clear(self, namespace: str) -> None:
        """Полностью очистить пространство имён."""
        with self._lock:
            if namespace in self._data:
                self._data.pop(namespace)
                self._save()


# Глобальный инстанс. Импортируется и loader'ом, и модулями через self.db.
db = KeyValueDB()


__all__ = ["KeyValueDB", "db"]
