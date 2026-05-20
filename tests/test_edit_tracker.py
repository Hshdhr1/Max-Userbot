"""Тесты для modules/examples/edit_tracker.py."""

import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import KeyValueDB  # noqa: E402


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "edit_tracker_test_target",
        ROOT / "modules" / "examples" / "edit_tracker.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeMessage:
    """Минимум API MaxMessage для теста."""

    def __init__(self, chat_id: int, msg: dict, sender_id: int = 1, outgoing: bool = False):
        self.chat_id = chat_id
        self.message = msg
        self.sender_id = sender_id
        self.is_outgoing = outgoing
        self._answers: list[str] = []

    async def answer(self, text: str) -> None:
        self._answers.append(text)


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = KeyValueDB(Path(self.tmp.name) / "db.json")
        et_mod = _load_module()
        self.cls = et_mod.EditTracker
        self.tracker = self.cls()
        run(self.tracker.client_ready(client=mock.MagicMock(), db=self.db))

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_edited(self):
        msg = FakeMessage(123, {
            "id": 555, "text": "новый", "oldText": "старый", "status": "EDITED",
        })
        run(self.tracker.watcher(msg))
        self.assertEqual(len(self.tracker._read(123, "edited")), 1)
        rec = self.tracker._read(123, "edited")[0]
        self.assertEqual(rec["text"], "новый")
        self.assertEqual(rec["prev_text"], "старый")

    def test_records_deleted(self):
        msg = FakeMessage(7, {"id": 1, "text": "до удаления", "status": "REMOVED"})
        run(self.tracker.watcher(msg))
        self.assertEqual(len(self.tracker._read(7, "deleted")), 1)

    def test_skips_normal_message(self):
        msg = FakeMessage(7, {"id": 1, "text": "обычное"})
        run(self.tracker.watcher(msg))
        self.assertEqual(self.tracker._read(7, "edited"), [])
        self.assertEqual(self.tracker._read(7, "deleted"), [])

    def test_skips_outgoing_by_default(self):
        msg = FakeMessage(9, {"id": 2, "status": "EDITED"}, outgoing=True)
        run(self.tracker.watcher(msg))
        self.assertEqual(self.tracker._read(9, "edited"), [])

    def test_tracks_outgoing_when_enabled(self):
        # Воспроизводим: пользователь вкл. track_outgoing.
        self.tracker.config["track_outgoing"] = True
        msg = FakeMessage(9, {"id": 2, "status": "EDITED"}, outgoing=True)
        run(self.tracker.watcher(msg))
        self.assertEqual(len(self.tracker._read(9, "edited")), 1)

    def test_caps_per_chat(self):
        self.tracker.config["max_per_chat"] = 3
        for i in range(10):
            msg = FakeMessage(1, {"id": i, "text": f"v{i}", "status": "EDITED"})
            run(self.tracker.watcher(msg))
        records = self.tracker._read(1, "edited")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[-1]["id"], 9)
        self.assertEqual(records[0]["id"], 7)


class CommandRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = KeyValueDB(Path(self.tmp.name) / "db.json")
        et_mod = _load_module()
        self.tracker = et_mod.EditTracker()
        run(self.tracker.client_ready(client=mock.MagicMock(), db=self.db))

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    async def _capture(m, t):
        m._answers.append(t)

    def test_edits_empty(self):
        msg = FakeMessage(42, {})
        with mock.patch("core.utils.answer", new=self._capture):
            run(self.tracker.edits(msg))
        self.assertTrue(any("Пусто" in a for a in msg._answers))

    def test_edits_with_content(self):
        run(self.tracker.watcher(FakeMessage(42, {
            "id": 1, "text": "X", "oldText": "Y", "status": "EDITED"
        })))
        msg = FakeMessage(42, {})
        with mock.patch("core.utils.answer", new=self._capture):
            run(self.tracker.edits(msg))
        out = "\n".join(msg._answers)
        self.assertIn("Редактирования", out)
        self.assertIn("X", out)
        self.assertIn("Y", out)

    def test_etclear_wipes_chat(self):
        run(self.tracker.watcher(FakeMessage(42, {"id": 1, "status": "EDITED"})))
        run(self.tracker.watcher(FakeMessage(42, {"id": 2, "status": "REMOVED"})))
        msg = FakeMessage(42, {})
        with mock.patch("core.utils.answer", new=self._capture):
            run(self.tracker.etclear(msg))
        self.assertEqual(self.tracker._read(42, "edited"), [])
        self.assertEqual(self.tracker._read(42, "deleted"), [])


if __name__ == "__main__":
    unittest.main()
