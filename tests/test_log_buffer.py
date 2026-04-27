"""Тесты ring-buffer для логов и SSE-фанаута."""

from __future__ import annotations

import asyncio
import logging
import unittest

from core.log_buffer import LogBuffer


class LogBufferTests(unittest.TestCase):
    def test_emit_appends_record(self) -> None:
        buf = LogBuffer(capacity=100)
        logger = logging.getLogger("test.lb.basic")
        logger.addHandler(buf)
        logger.setLevel(logging.INFO)
        try:
            logger.info("hello %s", "world")
            snap = buf.snapshot()
            self.assertEqual(len(snap), 1)
            self.assertEqual(snap[0]["msg"], "hello world")
            self.assertEqual(snap[0]["level"], "INFO")
            self.assertEqual(snap[0]["name"], "test.lb.basic")
            self.assertIn("ts_iso", snap[0])
        finally:
            logger.removeHandler(buf)

    def test_capacity_ringbuffer(self) -> None:
        buf = LogBuffer(capacity=5)
        logger = logging.getLogger("test.lb.ring")
        logger.addHandler(buf)
        logger.setLevel(logging.INFO)
        try:
            for i in range(10):
                logger.info("msg %d", i)
            snap = buf.snapshot()
            self.assertEqual(len(snap), 5)
            self.assertEqual([r["msg"] for r in snap], [f"msg {i}" for i in range(5, 10)])
        finally:
            logger.removeHandler(buf)

    def test_snapshot_limit(self) -> None:
        buf = LogBuffer(capacity=100)
        logger = logging.getLogger("test.lb.limit")
        logger.addHandler(buf)
        logger.setLevel(logging.INFO)
        try:
            for i in range(20):
                logger.info("m%d", i)
            self.assertEqual(len(buf.snapshot(limit=5)), 5)
            self.assertEqual(buf.snapshot(limit=5)[-1]["msg"], "m19")
        finally:
            logger.removeHandler(buf)

    def test_subscribe_receives_new_record(self) -> None:
        buf = LogBuffer(capacity=100)
        logger = logging.getLogger("test.lb.sub")
        logger.addHandler(buf)
        logger.setLevel(logging.INFO)

        async def run() -> dict:
            q = buf.subscribe()
            try:
                # Публикация после subscribe должна попасть в очередь.
                logger.info("subscribed event")
                entry = await asyncio.wait_for(q.get(), timeout=1.0)
                return entry
            finally:
                buf.unsubscribe(q)
                logger.removeHandler(buf)

        entry = asyncio.run(run())
        self.assertEqual(entry["msg"], "subscribed event")

    def test_unsubscribe_removes_queue(self) -> None:
        buf = LogBuffer()

        async def run() -> int:
            q = buf.subscribe()
            self.assertEqual(len(buf._subscribers), 1)
            buf.unsubscribe(q)
            return len(buf._subscribers)

        self.assertEqual(asyncio.run(run()), 0)


if __name__ == "__main__":
    unittest.main()
