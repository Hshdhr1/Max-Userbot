"""Hikka/Heroku-совместимая система модулей для Max-Userbot.

Модули пишутся как:

    from core import loader, utils

    @loader.tds  # optional: marker, оставлен для совместимости
    class HelloModule(loader.Module):
        \"\"\"Краткое описание модуля.\"\"\"

        strings = {"name": "Hello", "hi": "👋 hello, {name}"}

        def __init__(self):
            self.config = loader.ModuleConfig(
                loader.ConfigValue(
                    "default_name", "world", "Default name to greet",
                    validator=loader.validators.String(min_len=1, max_len=64),
                ),
                loader.ConfigValue(
                    "shouty", False, "ALL CAPS mode",
                    validator=loader.validators.Boolean(),
                ),
            )

        async def client_ready(self, client, db):
            self.client = client
            self.db = db

        @loader.command(ru_doc="[имя] - поздороваться")
        async def hello(self, message):
            name = utils.get_args_raw(message) or self.config["default_name"]
            text = self.strings["hi"].format(name=name)
            if self.config["shouty"]:
                text = text.upper()
            await utils.answer(message, text)

        @loader.watcher(only_incoming=True)
        async def watcher(self, message):
            if "ping" in message.text.lower():
                await message.reply("pong")

Loader.discover_module(path) импортирует файл, находит все subclass-ы
`loader.Module`, инстанциирует их, вызывает `client_ready`, вешает команды и
watcher'ы в существующий `ModuleRegistry`.
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any, Awaitable, Callable, Optional

from core.db import KeyValueDB
from core.message import MaxMessage

logger = logging.getLogger("max-userbot.loader")


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


class _BaseValidator:
    """База для конфиг-валидатора. validate() возвращает приведённое значение."""

    name: str = "any"

    def validate(self, value: Any) -> Any:  # pragma: no cover - переопределяется
        return value


class _Boolean(_BaseValidator):
    name = "boolean"

    def validate(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"1", "true", "yes", "on", "y", "+"}:
                return True
            if v in {"0", "false", "no", "off", "n", "-", ""}:
                return False
        raise ValueError(f"Cannot interpret {value!r} as boolean")


class _Integer(_BaseValidator):
    name = "integer"

    def __init__(self, minimum: Optional[int] = None, maximum: Optional[int] = None) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: Any) -> int:
        try:
            ivalue = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Cannot interpret {value!r} as integer") from exc
        if self.minimum is not None and ivalue < self.minimum:
            raise ValueError(f"Value {ivalue} < minimum {self.minimum}")
        if self.maximum is not None and ivalue > self.maximum:
            raise ValueError(f"Value {ivalue} > maximum {self.maximum}")
        return ivalue


class _Float(_BaseValidator):
    name = "float"

    def __init__(self, minimum: Optional[float] = None, maximum: Optional[float] = None) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def validate(self, value: Any) -> float:
        try:
            fvalue = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Cannot interpret {value!r} as float") from exc
        if self.minimum is not None and fvalue < self.minimum:
            raise ValueError(f"Value {fvalue} < minimum {self.minimum}")
        if self.maximum is not None and fvalue > self.maximum:
            raise ValueError(f"Value {fvalue} > maximum {self.maximum}")
        return fvalue


class _String(_BaseValidator):
    name = "string"

    def __init__(self, min_len: int = 0, max_len: Optional[int] = None) -> None:
        self.min_len = min_len
        self.max_len = max_len

    def validate(self, value: Any) -> str:
        s = str(value) if value is not None else ""
        if len(s) < self.min_len:
            raise ValueError(f"String too short ({len(s)} < {self.min_len})")
        if self.max_len is not None and len(s) > self.max_len:
            raise ValueError(f"String too long ({len(s)} > {self.max_len})")
        return s


class _Hidden(_String):
    """То же самое, что String, но в Web UI значение скрывается (placeholder)."""

    name = "hidden"


class _Choice(_BaseValidator):
    name = "choice"

    def __init__(self, choices: list[Any]) -> None:
        self.choices = list(choices)

    def validate(self, value: Any) -> Any:
        if value in self.choices:
            return value
        # Допускаем строковое сравнение (значения из формы Web UI всегда str).
        for choice in self.choices:
            if str(choice) == str(value):
                return choice
        raise ValueError(f"Value {value!r} not in choices {self.choices}")


class _RegExp(_BaseValidator):
    name = "regexp"

    def __init__(self, pattern: str) -> None:
        self.pattern = re.compile(pattern)

    def validate(self, value: Any) -> str:
        s = str(value) if value is not None else ""
        if not self.pattern.fullmatch(s):
            raise ValueError(f"Value {s!r} doesn't match {self.pattern.pattern!r}")
        return s


class _Validators:
    Boolean = _Boolean
    Integer = _Integer
    Float = _Float
    String = _String
    Hidden = _Hidden
    Choice = _Choice
    RegExp = _RegExp


validators = _Validators()


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


class ConfigValue:
    """Описание одной настройки модуля."""

    def __init__(
        self,
        key: str,
        default: Any = None,
        doc: str = "",
        validator: _BaseValidator | None = None,
    ) -> None:
        self.key = key
        self.default = default
        self.doc = doc
        self.validator = validator or _BaseValidator()


class ModuleConfig(dict):
    """Контейнер конфиг-значений с валидацией при записи."""

    def __init__(self, *values: ConfigValue) -> None:
        super().__init__()
        self._values: dict[str, ConfigValue] = {}
        for value in values:
            self._values[value.key] = value
            super().__setitem__(value.key, value.default)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._values:
            raise KeyError(f"Unknown config key {key!r}")
        try:
            value = self._values[key].validator.validate(value)
        except ValueError as exc:
            raise ValueError(f"Invalid value for '{key}': {exc}") from exc
        super().__setitem__(key, value)

    def schema(self) -> list[dict[str, Any]]:
        """Описание всех ключей — пригодится Web UI."""
        out: list[dict[str, Any]] = []
        for cfg in self._values.values():
            out.append(
                {
                    "key": cfg.key,
                    "default": cfg.default,
                    "doc": cfg.doc,
                    "validator": cfg.validator.name,
                }
            )
        return out

    def defaults(self) -> dict[str, Any]:
        return {key: cfg.default for key, cfg in self._values.items()}


# ---------------------------------------------------------------------------
# decorators / Module base
# ---------------------------------------------------------------------------


def command(
    *,
    name: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    ru_doc: Optional[str] = None,
    en_doc: Optional[str] = None,
    **_: Any,  # игнорируем uk_doc/de_doc/jp_doc/uwu_doc и т. д.
) -> Callable:
    """Помечает метод как команду. Имя команды = имя метода (или `name=...`)."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        func._is_command = True  # type: ignore[attr-defined]
        func._cmd_name = name or func.__name__  # type: ignore[attr-defined]
        func._cmd_aliases = list(aliases or [])  # type: ignore[attr-defined]
        # Документация: явный ru_doc/en_doc перевешивает docstring.
        func._cmd_doc = ru_doc or en_doc or (func.__doc__ or "").strip()  # type: ignore[attr-defined]
        return func

    return decorator


def watcher(
    *,
    only_incoming: bool = False,
    only_messages: bool = True,
    ignore_edited: bool = False,
) -> Callable:
    """Помечает метод как watcher (вызывается на каждом подходящем пакете)."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        func._is_watcher = True  # type: ignore[attr-defined]
        func._watcher_filters = {  # type: ignore[attr-defined]
            "only_incoming": only_incoming,
            "only_messages": only_messages,
            "ignore_edited": ignore_edited,
        }
        return func

    return decorator


def unrestricted(func: Callable) -> Callable:
    """No-op декоратор для совместимости с Heroku/Hikka."""
    func._unrestricted = True  # type: ignore[attr-defined]
    return func


def tds(cls: type) -> type:
    """Маркер «translatable strings» из Hikka. Нам i18n не нужен — оставляем no-op."""
    cls._is_tds = True  # type: ignore[attr-defined]
    return cls


class Module:
    """Базовый класс пользовательского модуля.

    Атрибуты, которые модуль может определить:
        - `strings`: dict[str, str]      — i18n строки + обязательный ключ "name"
        - `__doc__`                      — описание для меню .modules
        - `self.config`: ModuleConfig    — конфиг (через __init__)
        - `async def client_ready(client, db)` — async-init после connect
        - `async def on_unload()`        — cleanup
    """

    strings: dict[str, str] = {"name": "UnnamedModule"}

    # Заполняется loader'ом при инстанциации:
    db: KeyValueDB | None = None
    client: Any = None
    config: ModuleConfig | None = None

    # ------ удобные методы для модулей ---------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Получить значение из per-module DB."""
        if self.db is None:
            return default
        return self.db.get(self.strings.get("name", type(self).__name__), key, default)

    def set(self, key: str, value: Any) -> None:
        """Записать значение в per-module DB."""
        if self.db is None:
            return
        self.db.set(self.strings.get("name", type(self).__name__), key, value)

    def pop(self, key: str, default: Any = None) -> Any:
        if self.db is None:
            return default
        return self.db.pop(self.strings.get("name", type(self).__name__), key, default)


# ---------------------------------------------------------------------------
# discovery / registration
# ---------------------------------------------------------------------------


# Глобальный список инстансов модулей (нужен, чтобы on_unload звать при выгрузке).
_loaded_modules: list[Module] = []


def _iter_module_classes(py_module: Any) -> list[type[Module]]:
    """Найти все subclasses Module в импортированном python-модуле."""
    out: list[type[Module]] = []
    for _name, obj in inspect.getmembers(py_module, inspect.isclass):
        if obj is Module:
            continue
        if issubclass(obj, Module) and obj.__module__ == py_module.__name__:
            out.append(obj)
    return out


def _build_bot_module(instance: Module) -> Any:
    """Построить BotModule (старая dataclass-структура) для существующего реестра."""
    from userbot import BotModule, ModuleCommand

    name = instance.strings.get("name", type(instance).__name__)
    description = (instance.__class__.__doc__ or "").strip().split("\n", 1)[0] or name
    commands: list[ModuleCommand] = []
    for _, member in inspect.getmembers(instance, predicate=inspect.iscoroutinefunction):
        if not getattr(member, "_is_command", False):
            continue
        cmd_name = getattr(member, "_cmd_name", member.__name__)
        cmd_doc = getattr(member, "_cmd_doc", "") or member.__name__
        cmd_aliases = list(getattr(member, "_cmd_aliases", []))
        commands.append(ModuleCommand(name=cmd_name, description=cmd_doc, aliases=cmd_aliases))

    default_config: dict[str, Any] = {}
    if isinstance(instance.config, ModuleConfig):
        default_config = instance.config.defaults()

    return BotModule(
        name=name,
        description=description,
        commands=commands,
        builtin=False,
        default_config=default_config,
    )


def _passes_watcher_filter(filters: dict[str, bool], message: MaxMessage) -> bool:
    if filters.get("only_incoming") and message.is_outgoing:
        return False
    if filters.get("ignore_edited") and message.is_edited:
        return False
    if filters.get("only_messages", True) and message.opcode != 128:
        return False
    return True


def register_instance(instance: Module, registry: Any) -> None:
    """Зарегистрировать инстанс класса-модуля в `ModuleRegistry`."""
    bot_module = _build_bot_module(instance)
    registry.register_module(bot_module)
    name_lower = bot_module.name.lower()

    # ---- команды ------------------------------------------------------------
    for _, member in inspect.getmembers(instance, predicate=inspect.iscoroutinefunction):
        if not getattr(member, "_is_command", False):
            continue
        cmd_name = getattr(member, "_cmd_name", member.__name__).lower()
        aliases = [a.lower() for a in getattr(member, "_cmd_aliases", [])]

        async def handler(ctx, _chat_id, _message_id, _arg, _bound=member, _ctx=None):
            # `dynamic_commands` API уже устарел в части ctx-arg, но мы его
            # используем для обратной совместимости. Для новых команд мы
            # вызываем диспетчер через `dispatch_command` ниже, где у нас уже
            # есть готовый MaxMessage.
            return ""  # not used — мы переопределяем dispatch ниже

        # Регистрируем как dynamic_command, чтобы появилось в .modules / .help.
        registry.register_dynamic_command(cmd_name, handler)
        # Связь имя → инстанс/метод сохраняем в отдельной таблице.
        registry.command_to_module[cmd_name] = name_lower
        registry.class_commands[cmd_name] = (instance, member)
        for alias in aliases:
            registry.command_to_module[alias] = name_lower
            registry.class_commands[alias] = (instance, member)

    # ---- watcher'ы ----------------------------------------------------------
    for _, member in inspect.getmembers(instance, predicate=inspect.iscoroutinefunction):
        if not getattr(member, "_is_watcher", False):
            continue
        filters = getattr(member, "_watcher_filters", {})

        async def watcher_callback(client, packet, _bound=member, _filters=filters):
            try:
                msg = MaxMessage(client, packet, registry=registry)
                if not _passes_watcher_filter(_filters, msg):
                    return
                await _bound(msg)
            except Exception:  # noqa: BLE001
                logger.exception("Watcher %s failed", _bound.__qualname__)

        registry.register_watcher(watcher_callback)


async def discover_and_register(py_module: Any, registry: Any, client: Any, db: KeyValueDB) -> list[Module]:
    """Сканирует импортированный python-модуль на классы `Module`,
    инстанциирует их, вызывает `client_ready` и регистрирует в реестре.

    Возвращает список созданных инстансов.
    """
    instances: list[Module] = []
    for cls in _iter_module_classes(py_module):
        try:
            instance = cls()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to instantiate %s", cls.__name__)
            continue
        instance.db = db
        instance.client = client
        client_ready = getattr(instance, "client_ready", None)
        if callable(client_ready):
            try:
                result = client_ready(client, db)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("client_ready failed for %s", cls.__name__)
                continue
        register_instance(instance, registry)
        _loaded_modules.append(instance)
        instances.append(instance)
        logger.info("Loaded class-module: %s", instance.strings.get("name", cls.__name__))
    return instances


async def dispatch_command(
    instance_method: tuple[Module, Callable[..., Awaitable[Any]]],
    message: MaxMessage,
) -> bool:
    """Вызвать команду нового стиля. Возвращает True, если команда отработала."""
    instance, method = instance_method
    try:
        await method(message)
    except Exception:  # noqa: BLE001
        logger.exception("Command %s failed", method.__qualname__)
        try:
            await message.edit("❌ Внутренняя ошибка модуля. См. логи.")
        except Exception:  # noqa: BLE001
            pass
        return False
    return True


async def on_unload_all() -> None:
    """Вызвать on_unload у всех загруженных модулей (graceful shutdown)."""
    for instance in list(_loaded_modules):
        on_unload = getattr(instance, "on_unload", None)
        if not callable(on_unload):
            continue
        try:
            result = on_unload()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001
            logger.exception("on_unload failed for %s", type(instance).__name__)


__all__ = [
    "ConfigValue",
    "Module",
    "ModuleConfig",
    "command",
    "discover_and_register",
    "dispatch_command",
    "on_unload_all",
    "register_instance",
    "tds",
    "unrestricted",
    "validators",
    "watcher",
]
