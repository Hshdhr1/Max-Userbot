"""Тесты Hikka-style loader'а."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


from core import loader  # noqa: E402
from core.db import KeyValueDB  # noqa: E402
from core.message import MaxMessage  # noqa: E402


class ValidatorTests(unittest.TestCase):
    def test_boolean(self) -> None:
        v = loader.validators.Boolean()
        self.assertTrue(v.validate("yes"))
        self.assertFalse(v.validate("0"))
        self.assertFalse(v.validate(""))
        with self.assertRaises(ValueError):
            v.validate("totally not a bool")

    def test_integer_bounds(self) -> None:
        v = loader.validators.Integer(minimum=0, maximum=10)
        self.assertEqual(v.validate("5"), 5)
        with self.assertRaises(ValueError):
            v.validate(-1)
        with self.assertRaises(ValueError):
            v.validate(11)

    def test_float_bounds(self) -> None:
        v = loader.validators.Float(minimum=0.0, maximum=2.0)
        self.assertEqual(v.validate("1.5"), 1.5)
        with self.assertRaises(ValueError):
            v.validate(-0.1)

    def test_string_len(self) -> None:
        v = loader.validators.String(min_len=2, max_len=4)
        self.assertEqual(v.validate("ab"), "ab")
        with self.assertRaises(ValueError):
            v.validate("a")
        with self.assertRaises(ValueError):
            v.validate("toolong")

    def test_choice(self) -> None:
        v = loader.validators.Choice(["a", "b", 1])
        self.assertEqual(v.validate("a"), "a")
        self.assertEqual(v.validate("1"), 1)  # str→int normalization
        with self.assertRaises(ValueError):
            v.validate("c")


class ModuleConfigTests(unittest.TestCase):
    def test_defaults_and_validation(self) -> None:
        cfg = loader.ModuleConfig(
            loader.ConfigValue("ratio", 1.0, "doc",
                               validator=loader.validators.Float(minimum=0.0, maximum=2.0)),
            loader.ConfigValue("name", "alice", "doc",
                               validator=loader.validators.String(min_len=1)),
        )
        self.assertEqual(cfg["ratio"], 1.0)
        self.assertEqual(cfg["name"], "alice")
        cfg["ratio"] = 0.5
        self.assertEqual(cfg["ratio"], 0.5)
        with self.assertRaises(ValueError):
            cfg["ratio"] = 5.0
        with self.assertRaises(KeyError):
            cfg["unknown"] = 1
        schema = cfg.schema()
        self.assertEqual({s["key"] for s in schema}, {"ratio", "name"})


class KeyValueDBTests(unittest.TestCase):
    def test_set_get_pop_clear(self) -> None:
        path = Path("/tmp/test_max_db.json")
        path.unlink(missing_ok=True)
        try:
            db = KeyValueDB(path)
            db.set("ns", "k1", "v1")
            db.set("ns", "k2", [1, 2, 3])
            self.assertEqual(db.get("ns", "k1"), "v1")
            self.assertEqual(db.get("ns", "k2"), [1, 2, 3])
            self.assertEqual(db.get("ns", "missing", "fallback"), "fallback")

            # persist
            db2 = KeyValueDB(path)
            self.assertEqual(db2.get("ns", "k1"), "v1")

            self.assertEqual(db.pop("ns", "k1"), "v1")
            self.assertIsNone(db.get("ns", "k1"))

            db.clear("ns")
            self.assertEqual(db.all("ns"), {})
        finally:
            path.unlink(missing_ok=True)


class DiscoveryTests(unittest.TestCase):
    def test_discover_and_register_a_simple_module(self) -> None:
        # Используем встроенный пример KeyScanner.
        from userbot import ModuleRegistry

        spec = importlib.util.spec_from_file_location(
            "_test_keyscanner",
            ROOT / "modules" / "examples" / "keyscanner_example.py",
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        registry = ModuleRegistry()
        path = Path("/tmp/test_max_db_disc.json")
        path.unlink(missing_ok=True)
        try:
            db = KeyValueDB(path)
            instances = asyncio.run(
                loader.discover_and_register(mod, registry, client=None, db=db)
            )
            self.assertEqual(len(instances), 1)
            ks = instances[0]
            self.assertEqual(ks.strings["name"], "KeyScannerExample")
            # config & defaults
            self.assertTrue(isinstance(ks.config["watch_edits"], bool))
            self.assertGreater(int(ks.config["min_key_length"]), 0)
            # commands registered
            for cmd_name in ("ksauto", "ksstat", "ksstats", "ksclear", "kscan"):
                self.assertIn(cmd_name, registry.class_commands)
                self.assertIn(cmd_name, registry.command_to_module)
            # watcher registered
            self.assertEqual(len(registry.packet_watchers), 1)
            # bot module appears in registry
            self.assertIn("keyscannerexample", registry.modules)
        finally:
            path.unlink(missing_ok=True)


class DispatchTests(unittest.TestCase):
    def test_dispatch_invokes_method_with_message(self) -> None:
        async def run() -> None:
            class Demo(loader.Module):
                strings = {"name": "Demo"}

                @loader.command()
                async def hello(self, message: MaxMessage) -> None:
                    await message.edit(f"hi:{message.text}")

            instance = Demo()
            method = next(
                m for m in (Demo.hello,) if getattr(m, "_is_command", False)
            )
            client = MagicMock()
            packet = {"opcode": 128, "payload": {"chatId": 1, "message": {"id": 2, "text": "hello"}}}
            msg = MaxMessage(client, packet)

            # Patch userbot.edit_message to not require a real client.
            import userbot

            sentinel = AsyncMock()
            userbot.edit_message = sentinel  # type: ignore[assignment]

            ok = await loader.dispatch_command((instance, method.__get__(instance, Demo)), msg)
            self.assertTrue(ok)
            sentinel.assert_awaited_once()
            args, _ = sentinel.call_args
            self.assertEqual(args[1], 1)
            self.assertEqual(args[2], 2)
            self.assertEqual(args[3], "hi:hello")

        asyncio.run(run())


class WatcherFilterTests(unittest.TestCase):
    def test_only_incoming_filter(self) -> None:
        class WatcherMod(loader.Module):
            strings = {"name": "WatcherMod"}
            calls: list[str] = []

            @loader.watcher(only_incoming=True)
            async def watcher(self, message: MaxMessage) -> None:
                WatcherMod.calls.append(message.text)

        from userbot import ModuleRegistry

        registry = ModuleRegistry()
        instance = WatcherMod()
        loader.register_instance(instance, registry)

        async def run() -> None:
            client = MagicMock()
            # outgoing message — должно быть отброшено
            outgoing = {"opcode": 128, "payload": {"chatId": 1, "outgoing": True, "message": {"id": 1, "text": "OUT"}}}
            await registry.packet_watchers[0](client, outgoing)
            # incoming message
            incoming = {"opcode": 128, "payload": {"chatId": 1, "message": {"id": 2, "text": "IN"}}}
            await registry.packet_watchers[0](client, incoming)
            # non-message opcode — должно быть отброшено
            other = {"opcode": 200, "payload": {"chatId": 1, "message": {"id": 3, "text": "X"}}}
            await registry.packet_watchers[0](client, other)

        asyncio.run(run())
        self.assertEqual(WatcherMod.calls, ["IN"])


class UtilsTests(unittest.TestCase):
    def test_get_args_raw(self) -> None:
        from core.utils import get_args_raw

        client = MagicMock()
        msg = MaxMessage(
            client,
            {"opcode": 128, "payload": {"chatId": 1, "message": {"id": 1, "text": ".g foo bar baz"}}},
        )
        self.assertEqual(get_args_raw(msg), "foo bar baz")
        msg2 = MaxMessage(
            client,
            {"opcode": 128, "payload": {"chatId": 1, "message": {"id": 1, "text": ".g"}}},
        )
        self.assertEqual(get_args_raw(msg2), "")


if __name__ == "__main__":
    unittest.main()
