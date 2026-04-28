"""Базовые smoke-тесты Max Userbot.

Проверяют, что основные компоненты импортируются и не падают при базовых сценариях.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class ConfigStoreTests(unittest.TestCase):
    def test_config_store_handles_missing_file(self) -> None:
        from userbot import ConfigStore

        store = ConfigStore(Path("/tmp/__not_a_real_path__.json"))
        store.load()  # не должно падать
        self.assertEqual(store.data.prefix, ".")

    def test_config_store_ignores_unknown_keys(self) -> None:
        from userbot import ConfigStore

        path = Path("/tmp/userbot_test_config.json")
        path.write_text(json.dumps({"prefix": "!", "totally_unknown_key": 42}))
        try:
            store = ConfigStore(path)
            store.load()
            self.assertEqual(store.data.prefix, "!")
        finally:
            path.unlink(missing_ok=True)

    def test_config_store_ignores_malformed_json(self) -> None:
        from userbot import ConfigStore

        path = Path("/tmp/userbot_test_bad_config.json")
        path.write_text("{not really json")
        try:
            store = ConfigStore(path)
            store.load()
            # Должен остаться дефолтным префиксом, без исключения.
            self.assertEqual(store.data.prefix, ".")
        finally:
            path.unlink(missing_ok=True)


class BotModuleTests(unittest.TestCase):
    def test_default_config_attribute_exists(self) -> None:
        from userbot import BotModule, ModuleCommand

        module = BotModule(
            name="X",
            description="d",
            commands=[ModuleCommand(name="c", description="d")],
        )
        self.assertEqual(module.default_config, {})

    def test_default_config_can_be_set(self) -> None:
        from userbot import BotModule, ModuleCommand

        module = BotModule(
            name="X",
            description="d",
            commands=[ModuleCommand(name="c", description="d")],
            default_config={"foo": "bar"},
        )
        self.assertEqual(module.default_config, {"foo": "bar"})


class WebUIPanelTests(unittest.TestCase):
    def test_module_panel_renders_for_module_without_default_config(self) -> None:
        from userbot import BotModule, ModuleCommand, webui

        module = BotModule(
            name="DemoMod",
            description="Demo desc",
            commands=[ModuleCommand(name="demo", description="d")],
        )
        # default_config — пустой словарь, ранее был баг с обращением к атрибуту.
        html = webui._module_panel(module)
        self.assertIn("DemoMod", html)
        self.assertIn(".demo", html)


class MultiAccountManagerTests(unittest.TestCase):
    def test_connect_all_no_accounts(self) -> None:
        from core.multiaccount import MultiAccountManager

        mgr = MultiAccountManager()
        # _load_accounts вызовется внутри connect_all; для теста гарантируем чистый список.
        mgr.accounts = {}
        mgr.active_accounts = {}
        asyncio.run(mgr.connect_all())  # не должно падать

    def test_set_default_callback_propagates_to_authorized(self) -> None:
        from core.multiaccount import ActiveAccount, MultiAccountManager

        mgr = MultiAccountManager()
        mgr.accounts = {}
        mgr.active_accounts = {}

        client = MagicMock()
        client.set_callback = MagicMock(return_value=asyncio.sleep(0))
        api = MagicMock()
        active = ActiveAccount(
            label="x",
            phone="",
            client=client,
            api=api,
            authorized=True,
            callback=None,
        )
        mgr.active_accounts["x"] = active

        async def fake_callback(_client, _packet) -> None:  # pragma: no cover - не вызывается
            return None

        # До установки default callback — callback пустой.
        self.assertIsNone(active.callback)
        mgr.set_default_callback(fake_callback)
        # После — callback должен быть привязан.
        self.assertIs(active.callback, fake_callback)


if __name__ == "__main__":
    unittest.main()
