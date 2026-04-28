"""Ring-buffer для логов + asyncio fanout (для SSE-стрима в Web UI)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any


class LogBuffer(logging.Handler):
    """Logging-handler, складывающий записи в ring-buffer и раздающий новые
    подписчикам через asyncio.Queue (для SSE).
    """

    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.capacity = capacity
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        # Loop, в котором будут жить очереди. Заполняется при первом subscribe()
        # — потому что handler может быть подключён ещё до создания event loop'а.
        self._loop: asyncio.AbstractEventLoop | None = None

    # --- logging.Handler ---------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "ts": record.created,
                "ts_iso": time.strftime("%H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "name": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = self.format(record).split("\n", 1)[-1]
            self._records.append(entry)
            self._publish(entry)
        except Exception:  # noqa: BLE001 - never crash on logging
            self.handleError(record)

    # --- broadcast ---------------------------------------------------------

    def _publish(self, entry: dict[str, Any]) -> None:
        if not self._subscribers or self._loop is None:
            return
        for q in self._subscribers:
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, entry)
            except RuntimeError:
                # Loop закрыт — выкинем подписчика на следующем чтении.
                pass

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Получить очередь, в которую будут падать новые записи (только новые)."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    # --- snapshot ----------------------------------------------------------

    def snapshot(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Возвращает список последних записей (новые в конце)."""
        items = list(self._records)
        if limit is not None and len(items) > limit:
            items = items[-limit:]
        return items


# Глобальный инстанс. Прикручивается к корневому logger'у в userbot.py.
log_buffer = LogBuffer(capacity=int(__import__("os").getenv("MAX_LOG_BUFFER", "500")))


__all__ = ["LogBuffer", "log_buffer"]
