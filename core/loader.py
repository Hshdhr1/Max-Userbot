"""MaxUB module framework (Heroku-like philosophy, vkmax-native).

Цели:
- Класс-ориентированные модули
- Декораторы для регистрации
- Config API c валидаторами
- Без sync network внутри модулей
"""

from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


# ----------------------------- validators/config ------------------------------
class Validator:
    def validate(self, value: Any) -> Any:
        return value


class Hidden(Validator):
    pass


class Integer(Validator):
    def __init__(self, minimum: int | None = None, maximum: int | None = None):
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: Any) -> int:
        ivalue = int(value)
        if self.minimum is not None and ivalue < self.minimum:
            raise ValueError(f"Value must be >= {self.minimum}")
        if self.maximum is not None and ivalue > self.maximum:
            raise ValueError(f"Value must be <= {self.maximum}")
        return ivalue


@dataclass
class ConfigValue:
    key: str
    default: Any
    doc: Callable[[], str]
    validator: Validator | None = None


class ModuleConfig:
    def __init__(self, *values: ConfigValue):
        self._values = {v.key: v for v in values}
        self._state: dict[str, Any] = {v.key: v.default for v in values}

    def __getitem__(self, item: str) -> Any:
        return self._state[item]

    def __setitem__(self, item: str, value: Any) -> None:
        if item not in self._values:
            raise KeyError(item)
        validator = self._values[item].validator
        self._state[item] = validator.validate(value) if validator else value

    def as_dict(self) -> dict[str, Any]:
        return dict(self._state)


# -------------------------------- decorators ---------------------------------
def tds(cls):
    """Mark class as discoverable MaxUB module."""
    setattr(cls, "__maxub_module__", True)
    return cls


def command(name: str, doc: str = ""):
    def deco(func):
        setattr(func, "__maxub_command__", name)
        setattr(func, "__maxub_command_doc__", doc)
        return func

    return deco


# --------------------------------- base api ----------------------------------
class Module:
    strings = {"name": "BaseModule"}
    strings_ru = {}

    def __init__(self):
        self.config = ModuleConfig()
        self._db = None
        self._api = None

    async def client_ready(self, api, db):
        self._api = api
        self._db = db

    async def on_unload(self):
        return None

    def get_name(self) -> str:
        return self.strings.get("name", self.__class__.__name__)

    def get_commands(self) -> dict[str, Callable]:
        out: dict[str, Callable] = {}
        for _, member in inspect.getmembers(self, predicate=inspect.ismethod):
            cmd = getattr(member, "__maxub_command__", None)
            if cmd:
                out[cmd] = member
        return out


class ModuleManager:
    def __init__(self):
        self.modules: dict[str, Module] = {}
        self.commands: dict[str, tuple[Module, Callable]] = {}

    def register_instance(self, instance: Module) -> None:
        name = instance.get_name().lower()
        self.modules[name] = instance
        for cmd, fn in instance.get_commands().items():
            self.commands[cmd.lower()] = (instance, fn)

    async def init_modules(self, api=None, db=None) -> None:
        for module in self.modules.values():
            await module.client_ready(api, db)

    async def shutdown(self) -> None:
        for module in self.modules.values():
            await module.on_unload()

    def discover_from_path(self, path: Path) -> list[str]:
        loaded: list[str] = []
        if not path.exists():
            return loaded
        for file in path.glob("*.py"):
            if file.name.startswith("_"):
                continue
            loaded.extend(self.load_from_file(file))
        return loaded

    def load_from_file(self, path: Path) -> list[str]:
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load spec for {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        loaded: list[str] = []
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if getattr(obj, "__maxub_module__", False) and issubclass(obj, Module):
                instance = obj()
                self.register_instance(instance)
                loaded.append(instance.get_name())
        return loaded

    async def dispatch(self, command_name: str, message_ctx: dict, args_raw: str) -> str | None:
        target = self.commands.get(command_name.lower())
        if not target:
            return None
        module, fn = target
        result = await fn(message_ctx, args_raw)
        if result is None:
            return ""
        return str(result)

    def catalog(self) -> list[dict[str, Any]]:
        result = []
        for module in self.modules.values():
            cmds = list(module.get_commands().keys())
            result.append(
                {
                    "name": module.get_name(),
                    "commands": cmds,
                    "config": module.config.as_dict(),
                }
            )
        return sorted(result, key=lambda x: x["name"].lower())


# compatibility export
class validators:
    Hidden = Hidden
    Integer = Integer
