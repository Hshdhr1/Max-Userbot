"""Тесты для core.catalog — load/install/uninstall."""

import json
import tempfile
import unittest
from pathlib import Path

from core.catalog import (
    CatalogEntry,
    annotate_installed,
    install_module,
    load_catalog,
    uninstall_module,
)


SAMPLE = {
    "version": 1,
    "source": "test",
    "modules": [
        {
            "name": "Foo",
            "description": "demo",
            "version": "1.2",
            "filename": "foo.py",
            "url": "https://example.com/foo.py",
            "tags": ["demo"],
        },
        {
            "name": "BadName",
            "description": "no filename",
            "filename": "",
            "url": "https://example.com/x.py",
        },
    ],
}


class LoadCatalogTests(unittest.TestCase):
    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.json"
            p.write_text(json.dumps(SAMPLE))
            c = load_catalog(p)
        self.assertEqual(c.source, "test")
        self.assertEqual(len(c.modules), 2)
        self.assertEqual(c.modules[0].name, "Foo")
        self.assertEqual(c.modules[0].filename, "foo.py")

    def test_missing_file_returns_empty(self):
        c = load_catalog(Path("/nonexistent/__nope__.json"))
        self.assertEqual(c.modules, [])

    def test_corrupt_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.json"
            p.write_text("{not json")
            c = load_catalog(p)
        self.assertEqual(c.modules, [])


class InstallTests(unittest.TestCase):
    def test_install_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp) / "modules"
            mods.mkdir()
            entry = CatalogEntry(
                name="Foo",
                filename="foo_test.py",
                url="https://example.com/foo.py",
            )
            payload = b"# hello\n"

            def fake_fetch(url: str) -> bytes:
                self.assertEqual(url, entry.url)
                return payload

            res = install_module(entry, mods, fetcher=fake_fetch)
            self.assertEqual(res.status, "installed")
            self.assertEqual(res.bytes_written, len(payload))
            self.assertTrue((mods / "foo_test.py").exists())

            # second install with identical content == up_to_date
            res2 = install_module(entry, mods, fetcher=fake_fetch)
            self.assertEqual(res2.status, "up_to_date")

            # uninstall removes file
            res3 = uninstall_module("foo_test.py", mods)
            self.assertEqual(res3.status, "installed")
            self.assertFalse((mods / "foo_test.py").exists())

    def test_install_rejects_unsafe_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            entry = CatalogEntry(
                name="Bad",
                filename="../etc/passwd.py",
                url="https://example.com/x.py",
            )
            res = install_module(entry, mods, fetcher=lambda _: b"x")
            self.assertEqual(res.status, "error")

    def test_install_rejects_too_big(self):
        with tempfile.TemporaryDirectory() as tmp:
            mods = Path(tmp)
            entry = CatalogEntry(name="Big", filename="big.py", url="https://example.com/big.py")
            big_payload = b"x" * (1_000_001)
            res = install_module(entry, mods, fetcher=lambda _: big_payload)
            self.assertEqual(res.status, "error")
            self.assertIn("too big", res.error)

    def test_install_rejects_no_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = install_module(
                CatalogEntry(name="X", filename="x.py", url=""),
                Path(tmp),
                fetcher=lambda _: b"",
            )
            self.assertEqual(res.status, "error")

    def test_uninstall_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = uninstall_module("nope.py", Path(tmp))
            self.assertEqual(res.status, "error")


class AnnotateTests(unittest.TestCase):
    def test_installed_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "catalog.json"
            p.write_text(json.dumps(SAMPLE))
            c = load_catalog(p)
            mods = Path(tmp) / "modules"
            mods.mkdir()
            (mods / "foo.py").write_bytes(b"# stub")
            rows = annotate_installed(c, mods)
        names = {r["name"]: r["installed"] for r in rows}
        self.assertTrue(names["Foo"])
        self.assertFalse(names["BadName"])


if __name__ == "__main__":
    unittest.main()
