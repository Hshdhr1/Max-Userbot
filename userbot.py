import asyncio
import html
import importlib.util
import json
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from aiohttp import web
from vkmax.client import MaxClient
from vkmax.functions.messages import edit_message as _vkmax_edit_message
from vkmax.functions.messages import send_message as _vkmax_send_message

from core.catalog import (
    CatalogEntry,
    annotate_installed,
    install_module,
    load_catalog,
    uninstall_module,
)
from core.log_buffer import log_buffer
from core.security import is_dangerous, session_manager, verify_password

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%d.%m.%Y %H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("max-userbot")

# Подключаем ring-buffer к корневому logger'у — теперь всё, что попадает в
# logging.*, доступно через /api/logs (SSE) и /api/stats.
log_buffer.setLevel(logging.INFO)
log_buffer.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logging.getLogger().addHandler(log_buffer)

SESSION_FILE = Path("max_session.txt")
MODULES_DIR = Path("modules")
CONFIG_FILE = Path("userbot_config.json")
ACCOUNTS_FILE = Path("accounts.json")
DEFAULT_PREFIX = "."
START_TS = int(time.time())


# ------------------------------- stats / counters ----------------------------
@dataclass
class StatsCounters:
    """Глобальные счётчики, отображаемые в Web UI / .stats команде."""
    packets_in: int = 0
    packets_out: int = 0
    commands_handled: int = 0
    last_command_ts: float = 0.0
    last_error_ts: float = 0.0
    last_error_msg: str = ""


stats = StatsCounters()


async def send_message(client: MaxClient, chat_id: int, text: str, *args: Any, **kwargs: Any) -> Any:
    """Wrapper над vkmax send_message с инкрементом счётчика исходящих."""
    stats.packets_out += 1
    return await _vkmax_send_message(client, chat_id, text, *args, **kwargs)


async def edit_message(client: MaxClient, chat_id: int, message_id: int, text: str, *args: Any, **kwargs: Any) -> Any:
    """Wrapper над vkmax edit_message с инкрементом счётчика исходящих."""
    stats.packets_out += 1
    return await _vkmax_edit_message(client, chat_id, message_id, text, *args, **kwargs)


# ----------------------------- formatting helpers -----------------------------
def safe_markdown(text: str) -> str:
    """Very small safe markdown escaper for user-generated text."""
    escape_chars = r"*_`[]()~>#+-=|{}.!"
    return "".join(f"\\{c}" if c in escape_chars else c for c in text)


def to_html(text: str) -> str:
    return html.escape(text).replace("\n", "<br>")


# ------------------------------- data models ---------------------------------
@dataclass
class ModuleCommand:
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class BotModule:
    name: str
    description: str
    commands: list[ModuleCommand]
    builtin: bool = True
    hidden: bool = False
    version: str | None = None
    default_config: dict[str, Any] = field(default_factory=dict)


@dataclass
class UserbotConfig:
    prefix: str = DEFAULT_PREFIX
    markdown_enabled: bool = True
    random_reroute_guard: bool = True
    favorites_chat_id: int | None = None
    module_configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Пароль для опасных действий: scrypt-хеш + соль (hex). Если оба пусты —
    # бот при старте интерактивно спросит пароль и сохранит в конфиг.
    dangerous_password_hash: str = ""
    dangerous_password_salt: str = ""


@dataclass
class AccountEntry:
    label: str
    phone: str
    state: str = "pending_auth"
    device_id: str = ""
    token: str = ""


# ------------------------------- persistence ---------------------------------
class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = UserbotConfig()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Config file is unreadable, using defaults: %s", exc)
            return
        if not isinstance(payload, dict):
            logger.warning("Config file is not a JSON object, using defaults")
            return
        # Drop unknown keys so an older/newer schema doesn't crash startup.
        known = {f.name for f in fields(UserbotConfig)}
        filtered = {k: v for k, v in payload.items() if k in known}
        try:
            self.data = UserbotConfig(**filtered)
        except TypeError as exc:
            logger.warning("Config file has invalid types, using defaults: %s", exc)

    def save(self) -> None:
        self.path.write_text(json.dumps(asdict(self.data), ensure_ascii=False, indent=2), encoding="utf-8")


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[AccountEntry]:
        if not self.path.exists():
            return []
        try:
            rows = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Accounts file is unreadable, treating as empty: %s", exc)
            return []
        if not isinstance(rows, list):
            logger.warning("Accounts file is not a list, treating as empty")
            return []
        known = {f.name for f in fields(AccountEntry)}
        result: list[AccountEntry] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                result.append(AccountEntry(**{k: v for k, v in row.items() if k in known}))
            except TypeError as exc:
                logger.warning("Skipping invalid account row: %s", exc)
        return result

    def save(self, accounts: list[AccountEntry]) -> None:
        self.path.write_text(json.dumps([asdict(a) for a in accounts], ensure_ascii=False, indent=2), encoding="utf-8")

    def add_or_update(self, item: AccountEntry) -> None:
        accounts = self.load()
        for i, acc in enumerate(accounts):
            if acc.label.lower() == item.label.lower():
                accounts[i] = item
                self.save(accounts)
                return
        accounts.append(item)
        self.save(accounts)


# ------------------------------ api extensions -------------------------------
class MaxApiExtensions:
    def __init__(self, client: MaxClient):
        self.client = client

    async def send_raw(self, opcode: int, payload: dict) -> dict:
        if hasattr(self.client, "send_packet"):
            return await self.client.send_packet(opcode=opcode, payload=payload)
        raise RuntimeError("send_packet method is unavailable in current vkmax build")

    async def react(self, chat_id: int, message_id: str, emoji: str) -> dict:
        return await self.send_raw(
            178,
            {
                "chatId": chat_id,
                "messageId": str(message_id),
                "reaction": {"reactionType": "EMOJI", "id": emoji},
            },
        )

    async def update_profile(self, first_name: str | None = None, last_name: str | None = None, bio: str | None = None) -> dict:
        settings_payload: dict[str, Any] = {"user": {}}
        if first_name is not None:
            settings_payload["user"]["firstName"] = first_name
        if last_name is not None:
            settings_payload["user"]["lastName"] = last_name
        if bio is not None:
            settings_payload["user"]["bio"] = bio
        return await self.send_raw(22, {"settings": settings_payload})


# --------------------------------- weather -----------------------------------
class WeatherClient:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def get(self, city: str) -> str:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = f"https://ru.wttr.in/{city}?Q&T&format=3"
        async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status()
            return (await response.text()).strip()

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


# ------------------------------ module runtime -------------------------------
@dataclass
class BotContext:
    client: MaxClient
    registry: "ModuleRegistry"
    api: MaxApiExtensions
    config: ConfigStore

    async def send_opcode(self, opcode: int, payload: dict) -> dict:
        return await self.api.send_raw(opcode, payload)


PacketWatcher = Callable[[MaxClient, dict], Awaitable[None]]


@dataclass
class ModuleRegistry:
    modules: dict[str, BotModule] = field(default_factory=dict)
    command_to_module: dict[str, str] = field(default_factory=dict)
    dynamic_commands: dict[str, Callable[[BotContext, int, int, str], Awaitable[str]]] = field(default_factory=dict)
    packet_watchers: list[PacketWatcher] = field(default_factory=list)
    # Hikka-совместимые команды: имя -> (instance, async-метод)
    class_commands: dict[str, tuple[Any, Callable[..., Awaitable[Any]]]] = field(default_factory=dict)

    def register_module(self, module: BotModule) -> None:
        key = module.name.lower()
        self.modules[key] = module
        for cmd in module.commands:
            self.command_to_module[cmd.name.lower()] = key
            for alias in cmd.aliases:
                self.command_to_module[alias.lower()] = key

    def register_dynamic_command(self, command_name: str, handler: Callable[[BotContext, int, int, str], Awaitable[str]]) -> None:
        self.dynamic_commands[command_name.lower()] = handler

    def register_watcher(self, callback: PacketWatcher) -> None:
        self.packet_watchers.append(callback)

    def preload_default_modules(self) -> None:
        def c(name: str, description: str, aliases: Optional[list[str]] = None) -> ModuleCommand:
            return ModuleCommand(name=name, description=description, aliases=aliases or [])

        defaults = [
            BotModule("Updater", "Обновляет юзербот", [
                c("autoupdate", "switch autoupdate state"), c("changelog", "Show changelog"), c("restart", "Перезагрузка"),
                c("rollback", "Откат обновлений"), c("source", "Ссылка на исходники"), c("ubstop", "Остановить юзербот"), c("update", "Скачать обновления"),
            ]),
            BotModule("Translator", "Переводит текст", [c("tr", ".tr [язык] [текст]")]),
            BotModule("Translations", "Processes internal translations", [c("dllangpack", "Внешний пак перевода"), c("setlang", "Изменить язык")]),
            BotModule("Tester", "Самотестирование", [c("clearlogs", "Очистить логи"), c("logs", "Отправить лог"), c("ping", "Пинг"), c("suspend", "Пауза")]),
            BotModule("Terminal", "Runs commands", [c("terminal", "Запустить команду"), c("terminate", "Убить процесс")]),
            BotModule("Settings", "Базовые настройки", [
                c("addalias", "Добавить алиас"), c("aliases", "Показать алиасы"), c("blacklist", "Отключить бота в чате"),
                c("blacklistuser", "Запретить юзеру"), c("cleardb", "Очистить БД"), c("clearmodule", "Очистить модуль"),
                c("delalias", "Удалить алиас"), c("heroku", "Версия"), c("installation", "Инструкция"), c("setprefix", "Сменить префикс"),
                c("togglecmd", "Вкл/выкл команду"), c("togglemod", "Вкл/выкл модуль"), c("unblacklist", "Включить бота"), c("unblacklistuser", "Разрешить юзера"),
            ]),
            BotModule("Presets", "Сборки модулей", [
                c("addtofolder", "Add to folder", ["af"]), c("aliasload", "Load aliases", ["al"]), c("folderload", "Load folder", ["fl"]),
                c("loadaliases", "Load aliases from file", ["la"]), c("loadpreset", "Load preset", ["lp"]), c("presets", "Пакеты модулей"),
                c("removefromfolder", "Remove from folder", ["rff"]),
            ]),
            BotModule("Loader", "Загружает модули", [
                c("addrepo", "Добавить репозиторий"), c("clearmodules", "Выгрузить установленные"), c("delrepo", "Удалить репозиторий"),
                c("dlmod", "Скачать модуль", ["dlm"]), c("loadmod", "Загрузить модуль", ["lm"]), c("ml", "Список модулей"), c("unloadmod", "Выгрузить модуль", ["ulm"]),
            ]),
            BotModule("HerokuWeb", "Web/Inline mode add account", [c("addacc", "Добавить аккаунт"), c("weburl", "Открыть Web UI")], version="v2.0.0"),
            BotModule("HerokuSettings", "Доп. настройки", [
                c("enable_core_protection", "Enable protection"), c("nonickchat", "NoNick chat"), c("nonickchats", "Список NoNick чатов"),
                c("nonickcmd", "NoNick cmd"), c("nonickcmds", "Список NoNick cmd"), c("nonickuser", "NoNick user"), c("nonickusers", "NoNick users"),
                c("remove_core_protection", "Disable protection"), c("settings", "Показать настройки"), c("watcherbl", "Watcher blacklist"), c("watcher", "Watcher rules"), c("watchers", "Список watchers"),
            ]),
            BotModule("HerokuSecurity", "Управление безопасностью", [
                c("delsgroup", "Удалить группу"), c("inlinesec", "Inline security"), c("newsgroup", "Создать группу"), c("owneradd", "Добавить owner"),
                c("ownerlist", "Список owner"), c("ownerrm", "Удалить owner"), c("querysec", "Toggle query security"), c("security", "Правила"),
                c("sgroup", "Инфо о группе"), c("sgroupadd", "Добавить в группу"), c("sgroupdel", "Удалить из группы"), c("sgroups", "Список групп"),
                c("tsec", "Добавить targeted security"), c("tsecclr", "Очистить targeted security"), c("tsecrm", "Удалить targeted security"),
            ]),
            BotModule("HerokuPluginSecurity", "Security for external modules", [c("external", "Ограничить модуль"), c("unexternal", "Разрешить модуль")]),
            BotModule("HerokuInfo", "Show userbot info", [c("info", "Инфо о боте"), c("ubinfo", "Что такое userbot")]),
            BotModule("HerokuConfig", "Интерактивный конфигуратор", [c("config", "Настроить модуль", ["cfg"]), c("fconfig", "Быстрый конфиг", ["fcfg"])]),
            BotModule("HerokuBackup", "Резервные копии", [c("backupall", "Общий бэкап"), c("backupdb", "Бэкап БД"), c("backupmods", "Бэкап модов"), c("restoreall", "Восстановить всё"), c("restoredb", "Восстановить БД"), c("restoremods", "Восстановить моды"), c("set_backup_period", "Период")]),
            BotModule("Help", "Помощь", [c("help", "Справка"), c("helphide", "Скрыть модуль"), c("support", "Чат поддержки")]),
            BotModule("Evaluator", "Выполняет код", [c("e", "Python eval", ["eval"]), c("ec", "C"), c("ecpp", "C++"), c("enode", "Node.js")]),
            BotModule("APILimiter", "API flood protection", [c("api_fw_protection", "Вкл/выкл защиту"), c("suspend_api_protect", "Пауза защиты")]),
        ]
        for module in defaults:
            self.register_module(module)

    @property
    def available_modules(self) -> list[BotModule]:
        return sorted([m for m in self.modules.values() if not m.hidden], key=lambda x: x.name.lower())

    @property
    def hidden_modules(self) -> list[BotModule]:
        return sorted([m for m in self.modules.values() if m.hidden], key=lambda x: x.name.lower())

    def module_config(self, config: UserbotConfig, module_name: str) -> dict[str, Any]:
        key = module_name.lower()
        if key not in config.module_configs:
            config.module_configs[key] = {}
        return config.module_configs[key]

    def render_modules(self) -> str:
        lines = ["<b>Система модулей</b>", f"{len(self.available_modules)} модулей доступно, {len(self.hidden_modules)} скрыто:", ""]
        for module in self.available_modules:
            suffix = f" ({module.version})" if module.version else ""
            lines.append(f"🪐 <b>{module.name}{suffix}</b>")
            lines.append(f"ℹ️ {module.description}")
            for cmd in module.commands:
                alias = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                lines.append(f"▫️ .{cmd.name}{alias} — {cmd.description}")
            lines.append("☝️ Это встроенный модуль. Вы не можете его выгрузить или заменить")
            lines.append("")
        return "\n".join(lines).strip()

    def get_module(self, query: str) -> BotModule | None:
        q = query.lower().strip()
        if q in self.modules:
            return self.modules[q]
        module_key = self.command_to_module.get(q)
        return self.modules.get(module_key) if module_key else None

    def toggle_hidden(self, name: str) -> bool | None:
        module = self.get_module(name)
        if not module:
            return None
        module.hidden = not module.hidden
        return module.hidden

    def try_unload(self, name: str) -> tuple[bool, str]:
        module = self.get_module(name)
        if not module:
            return False, "Модуль не найден"
        if module.builtin:
            return False, "Это встроенный модуль. Вы не можете его выгрузить или заменить"
        key = module.name.lower()
        del self.modules[key]
        self.command_to_module = {k: v for k, v in self.command_to_module.items() if v != key}
        return True, f"Модуль {module.name} выгружен"

    async def load_external_module(self, path_text: str) -> str:
        path = Path(path_text).expanduser().resolve()
        if not path.exists() or path.suffix != ".py":
            raise ValueError("Укажи путь к существующему .py модулю")
        if MODULES_DIR.resolve() not in path.parents and path.parent != MODULES_DIR.resolve():
            raise ValueError("Модуль должен быть из ./modules")

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось загрузить модуль")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        setup = getattr(module, "setup", None)
        if not callable(setup):
            raise ValueError("Модуль должен иметь setup(registry)")
        setup(self)
        return f"Модуль {path.name} загружен"


# --------------------------------- web ui ------------------------------------
class WebUIManager:
    def __init__(self, registry: ModuleRegistry, config_store: ConfigStore, account_store: AccountStore):
        self.registry = registry
        self.config_store = config_store
        self.account_store = account_store
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.host = os.getenv("MAX_WEBUI_HOST", "127.0.0.1")
        self.port = int(os.getenv("MAX_WEBUI_PORT", "8088"))

    def _module_panel(self, module: BotModule) -> str:
        module_conf = self.config_store.data.module_configs.get(module.name.lower(), {})
        default_conf = getattr(module, "default_config", None) or {}
        display_conf = {**default_conf, **module_conf}

        # ----- config form (MD3 filled text fields) ----------------------------
        rows_html = ""
        for k, v in display_conf.items():
            val = module_conf.get(k, v)
            rows_html += (
                "<form class='cfg-row' method='post' action='/api/config'>"
                "<div class='md3-textfield'>"
                f"<input id='{html.escape(module.name)}-{html.escape(k)}' "
                f"name='value' value='{html.escape(str(val))}' placeholder=' ' autocomplete='off'>"
                f"<label for='{html.escape(module.name)}-{html.escape(k)}'>{html.escape(k)}</label>"
                "</div>"
                f"<input type='hidden' name='module' value='{html.escape(module.name)}'>"
                f"<input type='hidden' name='key' value='{html.escape(k)}'>"
                "<button type='submit' class='md3-btn md3-btn--tonal'>"
                "<span class='material-symbols-outlined'>save</span>Save</button>"
                "</form>"
            )
        cfg_html = (
            rows_html
            if rows_html
            else "<p class='md3-empty'>Нет конфигов — добавь через <code>.cfg &lt;module&gt; &lt;key&gt; &lt;value&gt;</code> или API.</p>"
        )

        # ----- description -----------------------------------------------------
        doc_html = (
            f"<p class='md3-card__support'>{html.escape(module.description)}</p>"
            if module.description
            else ""
        )

        # ----- commands chips --------------------------------------------------
        commands_html = ""
        if module.commands:
            chips = ""
            for cmd in module.commands:
                aliases = (
                    f" <span class='md3-chip__sub'>· {html.escape(', '.join('.' + a for a in cmd.aliases))}</span>"
                    if cmd.aliases
                    else ""
                )
                chips += (
                    "<li class='md3-chip' "
                    f"title='{html.escape(cmd.description)}'>"
                    f"<code>.{html.escape(cmd.name)}</code>{aliases}"
                    "</li>"
                )
            commands_html = (
                "<details class='md3-details'>"
                f"<summary>Команды ({len(module.commands)})</summary>"
                f"<ul class='md3-chips'>{chips}</ul>"
                "</details>"
            )

        kind_badge = (
            "<span class='md3-badge md3-badge--builtin'>builtin</span>"
            if module.builtin
            else "<span class='md3-badge md3-badge--external'>external</span>"
        )

        return (
            "<article class='md3-card md3-card--filled'>"
            "<header class='md3-card__header'>"
            f"<h3 class='md3-card__title'>{html.escape(module.name)}</h3>"
            f"{kind_badge}"
            "</header>"
            f"{doc_html}"
            f"{commands_html}"
            "<h4 class='md3-card__section'>Конфигурация</h4>"
            f"<div class='md3-card__cfg'>{cfg_html}</div>"
            "</article>"
        )

    @staticmethod
    def _md3_css() -> str:
        """Material Design 3 / Material You CSS (tokens + components)."""
        return """
:root {
  --md-sys-typescale-display: 600 28px/36px Roboto, Arial, sans-serif;
  --md-sys-typescale-title:   500 16px/24px Roboto, Arial, sans-serif;
  --md-sys-typescale-body:    400 14px/20px Roboto, Arial, sans-serif;
  --md-sys-typescale-label:   500 12px/16px Roboto, Arial, sans-serif;
  --md-sys-state-hover: rgba(103, 80, 164, 0.08);
  --md-sys-state-press: rgba(103, 80, 164, 0.16);
  --md-sys-radius-xs: 4px;
  --md-sys-radius-sm: 8px;
  --md-sys-radius-md: 12px;
  --md-sys-radius-lg: 16px;
  --md-sys-radius-xl: 28px;
  --md-elev-1: 0 1px 3px rgba(0,0,0,.30), 0 1px 2px rgba(0,0,0,.15);
  --md-elev-2: 0 2px 6px rgba(0,0,0,.30), 0 1px 2px rgba(0,0,0,.15);
}
:root[data-theme="light"] {
  --md-sys-color-primary: #6750A4;
  --md-sys-color-on-primary: #FFFFFF;
  --md-sys-color-primary-container: #EADDFF;
  --md-sys-color-on-primary-container: #21005D;
  --md-sys-color-secondary-container: #E8DEF8;
  --md-sys-color-on-secondary-container: #1D192B;
  --md-sys-color-tertiary-container: #FFD8E4;
  --md-sys-color-on-tertiary-container: #31111D;
  --md-sys-color-background: #FEF7FF;
  --md-sys-color-on-background: #1D1B20;
  --md-sys-color-surface: #FEF7FF;
  --md-sys-color-surface-container: #F3EDF7;
  --md-sys-color-surface-container-high: #ECE6F0;
  --md-sys-color-surface-container-highest: #E6E0E9;
  --md-sys-color-on-surface: #1D1B20;
  --md-sys-color-on-surface-variant: #49454F;
  --md-sys-color-outline: #79747E;
  --md-sys-color-outline-variant: #CAC4D0;
  --md-sys-color-error: #B3261E;
  --md-sys-color-success: #146C2E;
}
:root[data-theme="dark"] {
  --md-sys-color-primary: #D0BCFF;
  --md-sys-color-on-primary: #381E72;
  --md-sys-color-primary-container: #4F378B;
  --md-sys-color-on-primary-container: #EADDFF;
  --md-sys-color-secondary-container: #4A4458;
  --md-sys-color-on-secondary-container: #E8DEF8;
  --md-sys-color-tertiary-container: #633B48;
  --md-sys-color-on-tertiary-container: #FFD8E4;
  --md-sys-color-background: #141218;
  --md-sys-color-on-background: #E6E1E5;
  --md-sys-color-surface: #141218;
  --md-sys-color-surface-container: #1D1B20;
  --md-sys-color-surface-container-high: #2B2930;
  --md-sys-color-surface-container-highest: #36343B;
  --md-sys-color-on-surface: #E6E1E5;
  --md-sys-color-on-surface-variant: #CAC4D0;
  --md-sys-color-outline: #938F99;
  --md-sys-color-outline-variant: #49454F;
  --md-sys-color-error: #F2B8B5;
  --md-sys-color-success: #74D690;
}

* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  font: var(--md-sys-typescale-body);
  background: var(--md-sys-color-background);
  color: var(--md-sys-color-on-background);
  min-height: 100vh;
  transition: background-color 200ms ease, color 200ms ease;
}
code, .md3-mono { font-family: 'Roboto Mono', ui-monospace, 'Consolas', monospace; }

/* ---- top app bar ------------------------------------------------------- */
.md3-app-bar {
  position: sticky; top: 0; z-index: 10;
  display: flex; align-items: center; gap: 16px;
  padding: 12px 24px;
  background: var(--md-sys-color-surface-container);
  border-bottom: 1px solid var(--md-sys-color-outline-variant);
  backdrop-filter: saturate(1.2);
}
.md3-app-bar__lead { display: flex; align-items: center; gap: 12px; min-width: 0; }
.md3-app-bar__icon {
  font-size: 28px;
  color: var(--md-sys-color-primary);
  background: var(--md-sys-color-primary-container);
  border-radius: 999px; padding: 8px;
}
.md3-app-bar__title { margin: 0; font: var(--md-sys-typescale-display); }
.md3-app-bar__subtitle {
  margin: 0; color: var(--md-sys-color-on-surface-variant);
  font: var(--md-sys-typescale-label);
}
.md3-app-bar__stats { display: flex; gap: 8px; margin-left: auto; flex-wrap: wrap; }
.md3-stat {
  display: inline-flex; flex-direction: column; align-items: flex-end;
  background: var(--md-sys-color-secondary-container);
  color: var(--md-sys-color-on-secondary-container);
  border-radius: var(--md-sys-radius-md); padding: 6px 12px; min-width: 80px;
}
.md3-stat b { font: var(--md-sys-typescale-title); }
.md3-stat small { font: var(--md-sys-typescale-label); opacity: .8; }
.md3-app-bar__actions { display: flex; gap: 4px; }
.md3-iconbtn {
  display: inline-flex; align-items: center; justify-content: center;
  width: 40px; height: 40px; border-radius: 999px;
  background: transparent; border: none; cursor: pointer;
  color: var(--md-sys-color-on-surface-variant);
  text-decoration: none;
  transition: background-color 120ms ease;
}
.md3-iconbtn:hover { background: var(--md-sys-state-hover); }
.md3-iconbtn:active { background: var(--md-sys-state-press); }

/* ---- shell layout ------------------------------------------------------ */
.md3-shell {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px; padding: 24px;
  max-width: 1480px; margin: 0 auto;
}
@media (max-width: 900px) {
  .md3-shell { grid-template-columns: 1fr; }
  .md3-rail { position: relative !important; max-height: none !important; }
}

/* ---- navigation rail --------------------------------------------------- */
.md3-rail {
  position: sticky; top: 88px;
  align-self: start;
  background: var(--md-sys-color-surface-container);
  border-radius: var(--md-sys-radius-xl);
  padding: 16px;
  max-height: calc(100vh - 110px);
  display: flex; flex-direction: column; gap: 12px;
  overflow: hidden;
}
.md3-rail__list {
  list-style: none; margin: 0; padding: 0;
  display: flex; flex-direction: column; gap: 4px;
  overflow-y: auto;
  scrollbar-width: thin;
}
.md3-rail__list::-webkit-scrollbar { width: 6px; }
.md3-rail__list::-webkit-scrollbar-thumb {
  background: var(--md-sys-color-outline-variant); border-radius: 3px;
}
.md3-rail__item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-radius: 999px;
  cursor: pointer; user-select: none;
  color: var(--md-sys-color-on-surface);
  font: var(--md-sys-typescale-label); font-size: 14px;
  transition: background-color 120ms ease, color 120ms ease;
}
.md3-rail__item:hover { background: var(--md-sys-state-hover); }
.md3-rail__item:active { background: var(--md-sys-state-press); }
.md3-rail__count {
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  border-radius: 999px; padding: 2px 10px; font-size: 12px;
}

/* ---- text fields (filled MD3) ----------------------------------------- */
.md3-textfield {
  position: relative; flex: 1;
  background: var(--md-sys-color-surface-container-highest);
  border-radius: var(--md-sys-radius-xs) var(--md-sys-radius-xs) 0 0;
  border-bottom: 1px solid var(--md-sys-color-outline);
  transition: border-color 120ms ease;
  min-width: 160px;
}
.md3-textfield:focus-within { border-bottom-color: var(--md-sys-color-primary); }
.md3-textfield input {
  width: 100%; padding: 22px 12px 8px;
  background: transparent; border: none; outline: none;
  color: var(--md-sys-color-on-surface);
  font: var(--md-sys-typescale-body); font-size: 14px;
}
.md3-textfield label {
  position: absolute; left: 12px; top: 16px;
  color: var(--md-sys-color-on-surface-variant);
  font: var(--md-sys-typescale-body); pointer-events: none;
  transition: top 120ms ease, font-size 120ms ease, color 120ms ease;
}
.md3-textfield input:focus + label,
.md3-textfield input:not(:placeholder-shown) + label {
  top: 4px; font-size: 11px; color: var(--md-sys-color-primary);
}

/* ---- buttons ---------------------------------------------------------- */
.md3-btn {
  display: inline-flex; align-items: center; gap: 8px;
  border: none; cursor: pointer;
  font: var(--md-sys-typescale-label); font-size: 14px;
  padding: 10px 24px; border-radius: 999px;
  transition: background-color 120ms ease, box-shadow 120ms ease;
  white-space: nowrap;
}
.md3-btn .material-symbols-outlined { font-size: 18px; }
.md3-btn--filled {
  background: var(--md-sys-color-primary);
  color: var(--md-sys-color-on-primary);
}
.md3-btn--filled:hover { box-shadow: var(--md-elev-1); filter: brightness(1.05); }
.md3-btn--tonal {
  background: var(--md-sys-color-secondary-container);
  color: var(--md-sys-color-on-secondary-container);
  padding: 8px 16px;
}
.md3-btn--tonal:hover { filter: brightness(1.05); }
.md3-btn--outlined {
  background: transparent;
  color: var(--md-sys-color-primary);
  border: 1px solid var(--md-sys-color-outline);
  padding: 8px 16px;
}
.md3-btn--outlined:hover { background: var(--md-sys-state-hover); }

/* ---- sections / cards -------------------------------------------------- */
.md3-section { display: flex; flex-direction: column; gap: 12px; margin-bottom: 32px; }
.md3-section__header h2 {
  margin: 0; font: var(--md-sys-typescale-display); font-size: 22px;
  display: flex; align-items: center; gap: 10px;
  color: var(--md-sys-color-on-surface);
}
.md3-section__header .material-symbols-outlined {
  font-size: 22px; color: var(--md-sys-color-primary);
}
.md3-card {
  background: var(--md-sys-color-surface-container);
  border-radius: var(--md-sys-radius-lg);
  padding: 20px; transition: box-shadow 200ms ease, transform 200ms ease;
}
.md3-card--elevated { box-shadow: var(--md-elev-1); }
.md3-card--elevated:hover { box-shadow: var(--md-elev-2); }
.md3-card--filled { background: var(--md-sys-color-surface-container-high); }
.md3-card--filled:hover { transform: translateY(-1px); box-shadow: var(--md-elev-1); }
.md3-card-grid {
  display: grid; gap: 16px;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
}
.md3-card__header {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  margin-bottom: 8px;
}
.md3-card__title { margin: 0; font: var(--md-sys-typescale-title); font-size: 18px; }
.md3-card__support { color: var(--md-sys-color-on-surface-variant); margin: 4px 0 16px; }
.md3-card__section {
  margin: 16px 0 8px;
  font: var(--md-sys-typescale-label);
  text-transform: uppercase; letter-spacing: .5px;
  color: var(--md-sys-color-on-surface-variant);
}
.md3-card__cfg { display: flex; flex-direction: column; gap: 12px; }
.md3-empty {
  margin: 8px 0; padding: 12px;
  background: var(--md-sys-color-surface-container-highest);
  border-radius: var(--md-sys-radius-sm);
  color: var(--md-sys-color-on-surface-variant);
  font: var(--md-sys-typescale-body); font-style: italic;
}
.md3-empty code {
  background: var(--md-sys-color-surface-container);
  padding: 2px 6px; border-radius: 4px; color: var(--md-sys-color-primary);
}

/* ---- chips / commands -------------------------------------------------- */
.md3-details summary {
  cursor: pointer; padding: 6px 0;
  color: var(--md-sys-color-on-surface-variant);
  font: var(--md-sys-typescale-label); font-size: 13px;
}
.md3-chips {
  list-style: none; margin: 8px 0 0; padding: 0;
  display: flex; flex-wrap: wrap; gap: 6px;
}
.md3-chip {
  background: var(--md-sys-color-surface-container-highest);
  border: 1px solid var(--md-sys-color-outline-variant);
  border-radius: 8px; padding: 4px 10px;
  font-size: 12px; color: var(--md-sys-color-on-surface);
}
.md3-chip code { color: var(--md-sys-color-primary); }
.md3-chip__sub { color: var(--md-sys-color-on-surface-variant); }

/* ---- forms ------------------------------------------------------------- */
.md3-form { display: flex; flex-direction: column; gap: 12px; margin-bottom: 16px; }
.md3-form--inline {
  flex-direction: row; align-items: stretch; flex-wrap: wrap;
}
.cfg-row {
  display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap;
}
.cfg-row .md3-textfield { min-width: 220px; }

/* ---- list rows --------------------------------------------------------- */
.md3-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.md3-list__row {
  display: flex; align-items: center; gap: 14px;
  padding: 12px 8px; border-radius: var(--md-sys-radius-md);
  transition: background-color 120ms ease;
}
.md3-list__row:hover { background: var(--md-sys-state-hover); }
.md3-list__avatar {
  font-size: 32px; color: var(--md-sys-color-primary);
}
.md3-list__primary { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
.md3-list__title { font: var(--md-sys-typescale-title); font-size: 15px; }
.md3-list__support { color: var(--md-sys-color-on-surface-variant); font-size: 13px; }
.md3-list__empty { padding: 16px; text-align: center; }

/* ---- badge ------------------------------------------------------------- */
.md3-badge {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font: var(--md-sys-typescale-label); font-size: 11px; letter-spacing: .3px;
}
.md3-badge--builtin {
  background: color-mix(in srgb, var(--md-sys-color-success) 18%, transparent);
  color: var(--md-sys-color-success);
}
.md3-badge--external {
  background: var(--md-sys-color-tertiary-container);
  color: var(--md-sys-color-on-tertiary-container);
}

/* ---- snackbar ---------------------------------------------------------- */
.md3-snackbar {
  position: fixed; bottom: -80px; left: 50%; transform: translateX(-50%);
  background: var(--md-sys-color-on-surface);
  color: var(--md-sys-color-surface);
  padding: 14px 24px; border-radius: var(--md-sys-radius-xs);
  font: var(--md-sys-typescale-body); font-size: 14px;
  box-shadow: var(--md-elev-2);
  transition: bottom 220ms ease;
  pointer-events: none;
  z-index: 100;
}
.md3-snackbar--open { bottom: 32px; }

/* ---- stats tiles ------------------------------------------------------- */
.md3-section__hint {
  margin-left: auto;
  font: var(--md-sys-typescale-label);
  color: var(--md-sys-color-on-surface-variant);
}
.md3-stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
.md3-tile {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  background: var(--md-sys-color-surface-container-high);
  border-radius: var(--md-sys-radius-md);
  transition: transform 200ms ease, box-shadow 200ms ease;
}
.md3-tile:hover { transform: translateY(-1px); box-shadow: var(--md-elev-1); }
.md3-tile .material-symbols-outlined {
  background: var(--md-sys-color-primary-container);
  color: var(--md-sys-color-on-primary-container);
  border-radius: 999px; padding: 8px; font-size: 22px;
}
.md3-tile b { display: block; font: var(--md-sys-typescale-display); font-size: 22px; line-height: 1.1; }
.md3-tile small { color: var(--md-sys-color-on-surface-variant); font-size: 12px; }
.md3-stat-error {
  display: flex; align-items: center; gap: 8px;
  margin: 12px 0 0; padding: 10px 12px;
  background: color-mix(in srgb, var(--md-sys-color-error) 15%, transparent);
  color: var(--md-sys-color-error);
  border-radius: var(--md-sys-radius-md);
  font-size: 13px;
}
.md3-stat-error[hidden] { display: none; }

/* ---- catalog cards ---------------------------------------------------- */
.md3-catalog__card {
  display: flex; flex-direction: column; gap: 12px;
  padding: 20px;
  background: var(--md-sys-color-surface-container-high);
  border-radius: 16px;
  box-shadow: var(--md-elev-0);
  transition: transform 120ms ease, box-shadow 120ms ease;
  border: 1px solid var(--md-sys-color-outline-variant);
}
.md3-catalog__card:hover { transform: translateY(-1px); box-shadow: var(--md-elev-1); }
.md3-catalog__head {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 12px;
}
.md3-catalog__title {
  font: var(--md-sys-typescale-title); margin: 0; color: var(--md-sys-color-on-surface);
}
.md3-catalog__meta {
  font: var(--md-sys-typescale-label);
  color: var(--md-sys-color-on-surface-variant);
}
.md3-catalog__desc {
  margin: 0; color: var(--md-sys-color-on-surface-variant);
  font: var(--md-sys-typescale-body);
}
.md3-catalog__tags { display: flex; flex-wrap: wrap; gap: 6px; }
.md3-catalog__tag {
  padding: 2px 10px; border-radius: 999px;
  background: var(--md-sys-color-secondary-container);
  color: var(--md-sys-color-on-secondary-container);
  font: var(--md-sys-typescale-label); font-size: 11px;
}
.md3-catalog__actions { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
.md3-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 999px;
  font: var(--md-sys-typescale-label); font-size: 11px;
}
.md3-badge--installed {
  background: var(--md-sys-color-tertiary-container);
  color: var(--md-sys-color-on-tertiary-container);
}
.md3-badge--available {
  background: var(--md-sys-color-surface-container);
  color: var(--md-sys-color-on-surface-variant);
}

/* ---- unlock modal ----------------------------------------------------- */
.md3-modal[hidden] { display: none; }
.md3-modal {
  position: fixed; inset: 0; z-index: 1000;
  display: flex; align-items: center; justify-content: center;
}
.md3-modal__scrim {
  position: absolute; inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(2px);
  cursor: pointer;
}
.md3-modal__sheet {
  position: relative;
  background: var(--md-sys-color-surface-container-highest);
  color: var(--md-sys-color-on-surface);
  border-radius: 28px;
  padding: 24px;
  width: min(92vw, 420px);
  display: flex; flex-direction: column; gap: 16px;
  box-shadow: var(--md-elev-3, 0 8px 24px rgba(0,0,0,0.2));
}
.md3-modal__sheet h3 {
  margin: 0; font: var(--md-sys-typescale-title);
}
.md3-modal__desc {
  margin: 0; font: var(--md-sys-typescale-body);
  color: var(--md-sys-color-on-surface-variant);
}
.md3-modal__actions {
  display: flex; justify-content: flex-end; gap: 8px;
}
.md3-modal__error {
  background: var(--md-sys-color-error-container);
  color: var(--md-sys-color-on-error-container);
  padding: 8px 12px; border-radius: 8px;
  font: var(--md-sys-typescale-label);
}
.md3-modal__error[hidden] { display: none; }

.md3-iconbtn .material-symbols-outlined.lock-unlocked {
  color: var(--md-sys-color-tertiary);
}

/* ---- logs viewer ------------------------------------------------------- */
.md3-logs__controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.md3-logs__card { padding: 0 !important; overflow: hidden; }
.md3-log-view {
  margin: 0;
  height: 360px; overflow: auto;
  padding: 14px 18px;
  background: var(--md-sys-color-surface-container-highest);
  font-family: 'Roboto Mono', ui-monospace, 'Consolas', monospace;
  font-size: 12.5px; line-height: 1.55;
  color: var(--md-sys-color-on-surface);
  white-space: pre-wrap; word-break: break-word;
  scrollbar-width: thin;
}
.md3-log-view::-webkit-scrollbar { width: 8px; }
.md3-log-view::-webkit-scrollbar-thumb { background: var(--md-sys-color-outline-variant); border-radius: 4px; }
.md3-log-view > span { display: block; }
.md3-log-ts { color: var(--md-sys-color-on-surface-variant); }
.md3-log-lvl {
  display: inline-block; min-width: 64px;
  padding: 0 6px; margin-right: 4px;
  border-radius: 4px;
  font-weight: 700; font-size: 11px;
  background: var(--md-sys-color-surface-container);
}
.md3-log-name { color: var(--md-sys-color-primary); margin-right: 4px; }
.md3-log-debug   .md3-log-lvl { color: var(--md-sys-color-on-surface-variant); }
.md3-log-info    .md3-log-lvl { color: var(--md-sys-color-primary); }
.md3-log-warning .md3-log-lvl { color: #FFB800; }
.md3-log-error,
.md3-log-critical { color: var(--md-sys-color-error); }
.md3-log-error    .md3-log-lvl,
.md3-log-critical .md3-log-lvl {
  background: color-mix(in srgb, var(--md-sys-color-error) 25%, transparent);
  color: var(--md-sys-color-error);
}
.md3-select {
  padding: 8px 12px;
  background: var(--md-sys-color-surface-container-highest);
  color: var(--md-sys-color-on-surface);
  border: 1px solid var(--md-sys-color-outline);
  border-radius: var(--md-sys-radius-sm);
  font: var(--md-sys-typescale-body); font-size: 13px;
  cursor: pointer;
}
.md3-select:focus { outline: 2px solid var(--md-sys-color-primary); outline-offset: 2px; }
"""

    async def index(self, request: web.Request) -> web.Response:
        modules = self.registry.available_modules
        cards = "".join(self._module_panel(m) for m in modules)

        rail_items = "".join(
            f"<li class='md3-rail__item' data-module='{html.escape(m.name)}'>"
            f"<span class='md3-rail__name'>{html.escape(m.name)}</span>"
            f"<span class='md3-rail__count'>{len(m.commands)}</span>"
            "</li>"
            for m in modules
        )

        accounts = self.account_store.load()
        if accounts:
            accounts_html = "".join(
                "<li class='md3-list__row'>"
                "<span class='md3-list__avatar material-symbols-outlined'>account_circle</span>"
                "<div class='md3-list__primary'>"
                f"<div class='md3-list__title'>{html.escape(a.label)}</div>"
                f"<div class='md3-list__support'>{html.escape(a.phone)}</div>"
                "</div>"
                f"<span class='md3-badge md3-badge--{ 'builtin' if a.state == 'authorized' else 'external'}'>"
                f"{html.escape(a.state)}</span>"
                "</li>"
                for a in accounts
            )
        else:
            accounts_html = "<li class='md3-empty md3-list__empty'>Нет подключённых аккаунтов.</li>"

        uptime = int(time.time()) - START_TS
        saved = request.query.get("saved")
        snackbar_text = ""
        if saved == "config":
            snackbar_text = "Конфиг сохранён"
        elif saved == "account":
            snackbar_text = "Аккаунт добавлен"

        html_page = f"""<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<meta name='theme-color' content='#1d192b' media='(prefers-color-scheme: dark)'>
<meta name='theme-color' content='#fef7ff' media='(prefers-color-scheme: light)'>
<title>Max Userbot · Console</title>
<link rel='preconnect' href='https://fonts.googleapis.com'>
<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>
<link rel='stylesheet'
      href='https://fonts.googleapis.com/css2?family=Roboto:wght@400;500;700&family=Roboto+Mono&display=swap'>
<link rel='stylesheet'
      href='https://fonts.googleapis.com/icon?family=Material+Symbols+Outlined'>
<style>{self._md3_css()}</style>
</head>
<body>
<header class='md3-app-bar'>
  <div class='md3-app-bar__lead'>
    <span class='material-symbols-outlined md3-app-bar__icon'>terminal</span>
    <div>
      <h1 class='md3-app-bar__title'>Max&nbsp;Userbot</h1>
      <p class='md3-app-bar__subtitle'>Console · Material You</p>
    </div>
  </div>
  <div class='md3-app-bar__stats'>
    <span class='md3-stat'><b>{len(modules)}</b><small>модулей</small></span>
    <span class='md3-stat'><b>{len(accounts)}</b><small>аккаунтов</small></span>
    <span class='md3-stat'><b>{uptime}s</b><small>uptime</small></span>
  </div>
  <div class='md3-app-bar__actions'>
    <button id='md3-lock-toggle' class='md3-iconbtn' aria-label='Опасные действия' title='Опасные действия'>
      <span class='material-symbols-outlined' id='md3-lock-icon'>lock</span>
    </button>
    <button id='md3-theme-toggle' class='md3-iconbtn' aria-label='Переключить тему'>
      <span class='material-symbols-outlined'>dark_mode</span>
    </button>
    <a class='md3-iconbtn' href='/health' target='_blank' aria-label='Health check'>
      <span class='material-symbols-outlined'>monitor_heart</span>
    </a>
  </div>
</header>

<div class='md3-modal' id='md3-unlock-modal' hidden>
  <div class='md3-modal__scrim' data-close='1'></div>
  <div class='md3-modal__sheet' role='dialog' aria-modal='true' aria-labelledby='md3-unlock-title'>
    <h3 id='md3-unlock-title'>Опасные действия</h3>
    <p id='md3-unlock-desc' class='md3-modal__desc'></p>
    <div class='md3-textfield'>
      <input id='md3-unlock-pass' type='password' placeholder=' ' autocomplete='current-password'>
      <label for='md3-unlock-pass'>Пароль</label>
    </div>
    <div class='md3-modal__error' id='md3-unlock-error' hidden></div>
    <div class='md3-modal__actions'>
      <button class='md3-btn md3-btn--text' data-close='1'>Отмена</button>
      <button class='md3-btn md3-btn--filled' id='md3-unlock-submit'>Открыть сессию</button>
    </div>
  </div>
</div>

<div class='md3-shell'>
  <nav class='md3-rail' aria-label='Модули'>
    <div class='md3-rail__search md3-textfield'>
      <input id='md3-search' placeholder=' ' autocomplete='off'>
      <label for='md3-search'>Поиск модулей</label>
    </div>
    <ul class='md3-rail__list'>{rail_items}</ul>
  </nav>

  <main class='md3-main'>
    <section class='md3-section' id='stats'>
      <header class='md3-section__header'>
        <h2><span class='material-symbols-outlined'>monitoring</span>Статистика</h2>
        <span class='md3-section__hint' id='md3-stats-hint'>обновляется каждые 2с</span>
      </header>
      <article class='md3-card md3-card--elevated'>
        <div class='md3-stat-grid' id='md3-stat-grid'>
          <div class='md3-tile' data-key='uptime_seconds'>
            <span class='material-symbols-outlined'>schedule</span>
            <div><b id='md3-stat-uptime'>—</b><small>uptime</small></div>
          </div>
          <div class='md3-tile' data-key='modules'>
            <span class='material-symbols-outlined'>extension</span>
            <div><b id='md3-stat-modules'>—</b><small>модулей</small></div>
          </div>
          <div class='md3-tile' data-key='commands'>
            <span class='material-symbols-outlined'>terminal</span>
            <div><b id='md3-stat-commands'>—</b><small>команд</small></div>
          </div>
          <div class='md3-tile' data-key='watchers'>
            <span class='material-symbols-outlined'>sensors</span>
            <div><b id='md3-stat-watchers'>—</b><small>watcher'ов</small></div>
          </div>
          <div class='md3-tile' data-key='accounts'>
            <span class='material-symbols-outlined'>group</span>
            <div><b id='md3-stat-accounts'>—</b><small>аккаунтов</small></div>
          </div>
          <div class='md3-tile' data-key='packets_in'>
            <span class='material-symbols-outlined'>arrow_downward</span>
            <div><b id='md3-stat-pktin'>—</b><small>входящих</small></div>
          </div>
          <div class='md3-tile' data-key='packets_out'>
            <span class='material-symbols-outlined'>arrow_upward</span>
            <div><b id='md3-stat-pktout'>—</b><small>исходящих</small></div>
          </div>
          <div class='md3-tile' data-key='commands_handled'>
            <span class='material-symbols-outlined'>check_circle</span>
            <div><b id='md3-stat-cmdhandled'>—</b><small>обработано</small></div>
          </div>
        </div>
        <p class='md3-stat-error' id='md3-stat-error' hidden>
          <span class='material-symbols-outlined'>error</span>
          <span id='md3-stat-error-msg'></span>
        </p>
      </article>
    </section>

    <section class='md3-section' id='logs'>
      <header class='md3-section__header'>
        <h2><span class='material-symbols-outlined'>article</span>Логи</h2>
        <div class='md3-logs__controls'>
          <select id='md3-log-level' class='md3-select'>
            <option value=''>Все уровни</option>
            <option value='DEBUG'>DEBUG</option>
            <option value='INFO' selected>INFO+</option>
            <option value='WARNING'>WARNING+</option>
            <option value='ERROR'>ERROR</option>
          </select>
          <button id='md3-log-pause' class='md3-btn md3-btn--tonal' type='button'>
            <span class='material-symbols-outlined'>pause</span><span>Пауза</span>
          </button>
          <button id='md3-log-clear' class='md3-btn md3-btn--outlined' type='button'>
            <span class='material-symbols-outlined'>delete_sweep</span>Очистить
          </button>
        </div>
      </header>
      <article class='md3-card md3-card--elevated md3-logs__card'>
        <pre id='md3-log-view' class='md3-log-view'>connecting…</pre>
      </article>
    </section>

    <section class='md3-section' id='catalog'>
      <header class='md3-section__header'>
        <h2><span class='material-symbols-outlined'>storefront</span>Каталог модулей</h2>
        <span class='md3-section__hint' id='md3-catalog-hint'>загрузка…</span>
      </header>
      <div class='md3-card-grid' id='md3-catalog-grid'>
        <div class='md3-empty md3-catalog__loading'>Загружаем каталог…</div>
      </div>
    </section>

    <section class='md3-section' id='accounts'>
      <header class='md3-section__header'>
        <h2><span class='material-symbols-outlined'>person</span>Аккаунты</h2>
      </header>
      <article class='md3-card md3-card--elevated'>
        <form class='md3-form md3-form--inline' method='post' action='/api/accounts'>
          <div class='md3-textfield'>
            <input id='acc-label' name='label' placeholder=' ' autocomplete='off' required>
            <label for='acc-label'>Метка (например, main)</label>
          </div>
          <div class='md3-textfield'>
            <input id='acc-phone' name='phone' placeholder=' ' autocomplete='off' required>
            <label for='acc-phone'>Телефон, +79990000000</label>
          </div>
          <button type='submit' class='md3-btn md3-btn--filled'>
            <span class='material-symbols-outlined'>add</span>Добавить
          </button>
        </form>
        <ul class='md3-list'>{accounts_html}</ul>
      </article>
    </section>

    <section class='md3-section' id='modules'>
      <header class='md3-section__header'>
        <h2><span class='material-symbols-outlined'>extension</span>Модули и конфиги</h2>
      </header>
      <div class='md3-card-grid' id='md3-cards'>{cards}</div>
    </section>
  </main>
</div>

<div id='md3-snackbar' class='md3-snackbar' role='status' aria-live='polite'></div>

<script>
(function() {{
  // ---- Material You theme toggle (light / dark) ----
  const KEY = 'md3-theme';
  const root = document.documentElement;
  const saved = localStorage.getItem(KEY);
  const sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (saved) root.setAttribute('data-theme', saved);
  else root.setAttribute('data-theme', sysDark ? 'dark' : 'light');

  const btn = document.getElementById('md3-theme-toggle');
  const updateIcon = () => {{
    const dark = root.getAttribute('data-theme') === 'dark';
    btn.querySelector('.material-symbols-outlined').textContent = dark ? 'light_mode' : 'dark_mode';
  }};
  updateIcon();
  btn.addEventListener('click', () => {{
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem(KEY, next);
    updateIcon();
  }});

  // ---- module search filter (rail + cards) ----
  const search = document.getElementById('md3-search');
  const rail = document.querySelectorAll('.md3-rail__item');
  const cards = document.querySelectorAll('.md3-card.md3-card--filled');
  search.addEventListener('input', () => {{
    const q = search.value.trim().toLowerCase();
    rail.forEach(el => {{
      const m = el.dataset.module.toLowerCase();
      el.style.display = (!q || m.includes(q)) ? '' : 'none';
    }});
    cards.forEach(card => {{
      const title = card.querySelector('.md3-card__title');
      const t = title ? title.textContent.trim().toLowerCase() : '';
      card.style.display = (!q || t.includes(q)) ? '' : 'none';
    }});
  }});

  // smooth scroll when clicking a rail item
  rail.forEach(el => {{
    el.addEventListener('click', () => {{
      const name = el.dataset.module;
      const card = Array.from(cards).find(c => {{
        const t = c.querySelector('.md3-card__title');
        return t && t.textContent.trim() === name;
      }});
      if (card) card.scrollIntoView({{behavior: 'smooth', block: 'start'}});
    }});
  }});

  // ---- snackbar ----
  const snack = document.getElementById('md3-snackbar');
  const showSnack = (text) => {{
    if (!text) return;
    snack.textContent = text;
    snack.classList.add('md3-snackbar--open');
    clearTimeout(showSnack._t);
    showSnack._t = setTimeout(() => snack.classList.remove('md3-snackbar--open'), 2400);
  }};
  showSnack({snackbar_text!r});

  // ---- stats polling ----
  const fmtUptime = (s) => {{
    s = Math.max(0, Math.floor(s));
    const d = Math.floor(s / 86400); s -= d*86400;
    const h = Math.floor(s / 3600); s -= h*3600;
    const m = Math.floor(s / 60); s -= m*60;
    const parts = [];
    if (d) parts.push(d + 'd');
    if (h || d) parts.push(h + 'h');
    if (m || h || d) parts.push(m + 'm');
    parts.push(s + 's');
    return parts.join(' ');
  }};
  const setText = (id, val) => {{
    const el = document.getElementById(id); if (el) el.textContent = val;
  }};
  const fetchStats = async () => {{
    try {{
      const r = await fetch('/api/stats', {{cache: 'no-store'}});
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const s = await r.json();
      setText('md3-stat-uptime', fmtUptime(s.uptime_seconds));
      setText('md3-stat-modules', s.modules);
      setText('md3-stat-commands', s.commands);
      setText('md3-stat-watchers', s.watchers);
      setText('md3-stat-accounts', s.accounts + ' / ' + s.accounts_authorized);
      setText('md3-stat-pktin', s.packets_in);
      setText('md3-stat-pktout', s.packets_out);
      setText('md3-stat-cmdhandled', s.commands_handled);
      const errBox = document.getElementById('md3-stat-error');
      if (s.last_error_msg) {{
        document.getElementById('md3-stat-error-msg').textContent = s.last_error_msg;
        errBox.hidden = false;
      }} else {{
        errBox.hidden = true;
      }}
    }} catch (e) {{
      const hint = document.getElementById('md3-stats-hint');
      if (hint) hint.textContent = 'offline · ' + e.message;
    }}
  }};
  fetchStats();
  setInterval(fetchStats, 2000);

  // ---- logs SSE ----
  const LEVEL_RANK = {{DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50}};
  const logView = document.getElementById('md3-log-view');
  const levelSelect = document.getElementById('md3-log-level');
  const pauseBtn = document.getElementById('md3-log-pause');
  const clearBtn = document.getElementById('md3-log-clear');
  let paused = false;
  let buffered = [];
  const MAX_LINES = 800;

  const renderLog = (entry) => {{
    const lvl = (entry.level || 'INFO').toUpperCase();
    const cls = 'md3-log-' + lvl.toLowerCase();
    const safe = (entry.msg || '').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c]);
    return `<span class="${{cls}}"><span class="md3-log-ts">${{entry.ts_iso || ''}}</span> <span class="md3-log-lvl">${{lvl}}</span> <span class="md3-log-name">${{(entry.name||'').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c])}}</span> ${{safe}}</span>`;
  }};

  const flushView = () => {{
    if (paused) return;
    const minLevel = LEVEL_RANK[levelSelect.value] || 0;
    const lines = buffered.filter(e => (LEVEL_RANK[(e.level||'').toUpperCase()] || 0) >= minLevel);
    if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES);
    logView.innerHTML = lines.map(renderLog).join('\\n');
    logView.scrollTop = logView.scrollHeight;
  }};

  const appendLog = (entry) => {{
    buffered.push(entry);
    if (buffered.length > MAX_LINES * 2) buffered.splice(0, buffered.length - MAX_LINES * 2);
    flushView();
  }};

  let es = null;
  const connect = () => {{
    if (es) es.close();
    es = new EventSource('/api/logs/stream');
    es.onmessage = (ev) => {{
      try {{ appendLog(JSON.parse(ev.data)); }} catch (_) {{}}
    }};
    es.onerror = () => {{
      // EventSource будет переподключаться автоматически.
    }};
  }};
  connect();

  levelSelect.addEventListener('change', flushView);
  pauseBtn.addEventListener('click', () => {{
    paused = !paused;
    pauseBtn.querySelector('.material-symbols-outlined').textContent = paused ? 'play_arrow' : 'pause';
    pauseBtn.querySelector('span:last-child').textContent = paused ? 'Возобновить' : 'Пауза';
    if (!paused) flushView();
  }});
  clearBtn.addEventListener('click', () => {{
    buffered = [];
    flushView();
  }});
}})();

// ---- catalog & unlock-modal ----
(function() {{
  const lockBtn = document.getElementById('md3-lock-toggle');
  const lockIcon = document.getElementById('md3-lock-icon');
  const modal = document.getElementById('md3-unlock-modal');
  const desc = document.getElementById('md3-unlock-desc');
  const passInput = document.getElementById('md3-unlock-pass');
  const errBox = document.getElementById('md3-unlock-error');
  const submitBtn = document.getElementById('md3-unlock-submit');
  const grid = document.getElementById('md3-catalog-grid');
  const hint = document.getElementById('md3-catalog-hint');

  let authState = {{ password_configured: false, unlocked: true }};
  let pendingAction = null;

  const refreshAuthIcon = () => {{
    if (!authState.password_configured) {{
      lockIcon.textContent = 'no_encryption';
      lockBtn.title = 'Пароль не задан';
      lockBtn.classList.remove('lock-unlocked');
    }} else if (authState.unlocked) {{
      lockIcon.textContent = 'lock_open';
      lockBtn.title = 'Сессия открыта — клик чтобы закрыть';
      lockIcon.classList.add('lock-unlocked');
    }} else {{
      lockIcon.textContent = 'lock';
      lockBtn.title = 'Сессия закрыта — клик чтобы открыть';
      lockIcon.classList.remove('lock-unlocked');
    }}
  }};

  const fetchAuth = async () => {{
    try {{
      const r = await fetch('/api/auth/status', {{credentials: 'same-origin'}});
      authState = await r.json();
    }} catch (_) {{}}
    refreshAuthIcon();
  }};

  const openModal = (action, message) => {{
    pendingAction = action;
    desc.textContent = message || 'Введите пароль для опасных действий.';
    errBox.hidden = true; errBox.textContent = '';
    passInput.value = '';
    modal.hidden = false;
    setTimeout(() => passInput.focus(), 50);
  }};
  const closeModal = () => {{
    modal.hidden = true;
    pendingAction = null;
  }};

  modal.addEventListener('click', (e) => {{
    if (e.target.dataset && e.target.dataset.close === '1') closeModal();
  }});
  document.addEventListener('keydown', (e) => {{
    if (!modal.hidden && e.key === 'Escape') closeModal();
  }});

  submitBtn.addEventListener('click', async () => {{
    const password = passInput.value;
    if (!password) {{ errBox.textContent = 'Введите пароль.'; errBox.hidden = false; return; }}
    submitBtn.disabled = true;
    try {{
      const fd = new FormData(); fd.append('password', password);
      const r = await fetch('/api/auth/unlock', {{method: 'POST', body: fd, credentials: 'same-origin'}});
      const data = await r.json().catch(() => ({{}}));
      if (!r.ok || data.ok !== true) {{
        errBox.textContent = data.reason === 'invalid' ? 'Неверный пароль.' : 'Не удалось открыть сессию.';
        errBox.hidden = false;
        return;
      }}
      authState.unlocked = true;
      refreshAuthIcon();
      const action = pendingAction;
      closeModal();
      if (typeof action === 'function') action();
    }} finally {{
      submitBtn.disabled = false;
    }}
  }});
  passInput.addEventListener('keydown', (e) => {{ if (e.key === 'Enter') submitBtn.click(); }});

  lockBtn.addEventListener('click', async () => {{
    if (!authState.password_configured) {{
      alert('Пароль не задан. Запустите бот в консоли — он попросит установить пароль при старте.');
      return;
    }}
    if (authState.unlocked) {{
      await fetch('/api/auth/lock', {{method: 'POST', credentials: 'same-origin'}});
      authState.unlocked = false;
      refreshAuthIcon();
    }} else {{
      openModal(null, 'Откройте сессию, чтобы выполнять опасные действия.');
    }}
  }});

  // ---- catalog ----
  const renderCard = (mod) => {{
    const card = document.createElement('article');
    card.className = 'md3-catalog__card';
    const tags = (mod.tags || []).map(t => `<span class="md3-catalog__tag">${{t}}</span>`).join('');
    const badge = mod.installed
      ? '<span class="md3-badge md3-badge--installed"><span class="material-symbols-outlined" style="font-size:14px">check_circle</span>Установлен</span>'
      : '<span class="md3-badge md3-badge--available">Доступен</span>';
    card.innerHTML = `
      <div class='md3-catalog__head'>
        <div>
          <h3 class='md3-catalog__title'>${{mod.name}}</h3>
          <div class='md3-catalog__meta'>v${{mod.version}}${{mod.author ? ' · ' + mod.author : ''}}</div>
        </div>
        ${{badge}}
      </div>
      <p class='md3-catalog__desc'>${{mod.description || ''}}</p>
      <div class='md3-catalog__tags'>${{tags}}</div>
      <div class='md3-catalog__actions'>
        <button class='md3-btn md3-btn--filled' data-action='install' data-name='${{mod.name}}' ${{mod.installed ? 'disabled' : ''}}>
          <span class='material-symbols-outlined'>download</span><span>${{mod.installed ? 'Установлено' : 'Установить'}}</span>
        </button>
        ${{mod.installed
          ? `<button class='md3-btn md3-btn--text' data-action='uninstall' data-name='${{mod.name}}'><span class='material-symbols-outlined'>delete</span><span>Удалить</span></button>`
          : ''}}
      </div>
    `;
    return card;
  }};

  const loadCatalog = async () => {{
    try {{
      const r = await fetch('/api/catalog', {{credentials: 'same-origin'}});
      if (!r.ok) throw new Error('http ' + r.status);
      const data = await r.json();
      grid.innerHTML = '';
      if (!data.modules || !data.modules.length) {{
        grid.innerHTML = '<div class="md3-empty">Каталог пуст. Задайте <code>MAX_CATALOG_URL</code> или отредактируйте <code>catalog.json</code>.</div>';
        hint.textContent = '0 модулей';
        return;
      }}
      data.modules.forEach(m => grid.appendChild(renderCard(m)));
      hint.textContent = `${{data.modules.length}} модулей · источник: ${{data.source || 'локальный'}}`;
    }} catch (e) {{
      grid.innerHTML = '<div class="md3-empty">Не удалось загрузить каталог.</div>';
      hint.textContent = 'offline';
    }}
  }};

  const callInstall = async (name) => {{
    const fd = new FormData(); fd.append('name', name);
    const r = await fetch('/api/catalog/install', {{method: 'POST', body: fd, credentials: 'same-origin'}});
    const data = await r.json().catch(() => ({{}}));
    if (r.status === 403) return 'locked';
    if (!r.ok || data.ok !== true) return 'error:' + (data.error || data.reason || r.status);
    return 'ok:' + data.status;
  }};
  const callUninstall = async (name) => {{
    const fd = new FormData(); fd.append('name', name);
    const r = await fetch('/api/catalog/uninstall', {{method: 'POST', body: fd, credentials: 'same-origin'}});
    const data = await r.json().catch(() => ({{}}));
    if (r.status === 403) return 'locked';
    if (!r.ok || data.ok !== true) return 'error:' + (data.error || data.reason || r.status);
    return 'ok';
  }};

  grid.addEventListener('click', async (e) => {{
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const name = btn.dataset.name;
    const action = btn.dataset.action;
    btn.disabled = true;
    const exec = async () => {{
      let res;
      if (action === 'install') res = await callInstall(name);
      else if (action === 'uninstall') res = await callUninstall(name);
      else res = 'error:unknown';
      if (res === 'locked') {{
        openModal(exec, action === 'install' ? `Введите пароль, чтобы установить «${{name}}».` : `Введите пароль, чтобы удалить «${{name}}».`);
      }} else if (res.startsWith('error:')) {{
        alert('Ошибка: ' + res.slice(6));
        btn.disabled = false;
      }} else {{
        await loadCatalog();
      }}
    }};
    await exec();
  }});

  fetchAuth();
  loadCatalog();
}})();
</script>
</body>
</html>
"""
        return web.Response(text=html_page, content_type="text/html")

    async def add_account(self, request: web.Request) -> web.Response:
        if not self._is_request_unlocked(request):
            raise web.HTTPFound("/?error=locked")
        data = await request.post()
        label = (data.get("label") or "").strip()
        phone = (data.get("phone") or "").strip()
        if not label or not phone:
            return web.Response(status=400, text="label and phone are required")
        self.account_store.add_or_update(AccountEntry(label=label, phone=phone, state="pending_auth"))
        # Также синхронизируем с MultiAccountManager, если он используется в текущем процессе.
        try:
            from core.multiaccount import multiaccount_manager  # local import to avoid cycle on bare imports
            if label not in multiaccount_manager.accounts:
                from core.multiaccount import AccountEntry as MultiAccountEntry
                multiaccount_manager.accounts[label] = MultiAccountEntry(label=label, phone=phone)
                multiaccount_manager._save_accounts()
        except Exception:  # noqa: BLE001 - синхронизация опциональна
            logger.debug("MultiAccountManager недоступен для синхронизации", exc_info=True)
        raise web.HTTPFound("/?saved=account")

    async def update_config(self, request: web.Request) -> web.Response:
        data = await request.post()
        module = (data.get("module") or "").strip().lower()
        key = (data.get("key") or "").strip()
        value = (data.get("value") or "").strip()
        if not module or not key:
            return web.Response(status=400, text="module and key are required")
        self.registry.module_config(self.config_store.data, module)[key] = value
        self.config_store.save()
        raise web.HTTPFound("/?saved=config")

    async def health(self, _: web.Request) -> web.Response:
        accounts = self.account_store.load()
        body = {
            "status": "ok",
            "uptime_seconds": int(time.time()) - START_TS,
            "modules": len(self.registry.available_modules),
            "accounts": len(accounts),
        }
        return web.json_response(body)

    async def stats_endpoint(self, _: web.Request) -> web.Response:
        accounts = self.account_store.load()
        commands = sum(len(m.commands) for m in self.registry.available_modules)
        body = {
            "uptime_seconds": int(time.time()) - START_TS,
            "modules": len(self.registry.available_modules),
            "accounts": len(accounts),
            "accounts_authorized": sum(1 for a in accounts if a.state == "authorized"),
            "commands": commands,
            "watchers": len(self.registry.packet_watchers),
            "class_commands": len(self.registry.class_commands),
            "packets_in": stats.packets_in,
            "packets_out": stats.packets_out,
            "commands_handled": stats.commands_handled,
            "last_command_ts": stats.last_command_ts,
            "last_error_ts": stats.last_error_ts,
            "last_error_msg": stats.last_error_msg,
        }
        return web.json_response(body)

    async def logs_history(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "200"))
        except ValueError:
            limit = 200
        return web.json_response({"records": log_buffer.snapshot(limit)})

    async def logs_stream(self, request: web.Request) -> web.StreamResponse:
        """Server-Sent Events стрим всех новых лог-записей."""
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        # Сначала пушим последние 100 строк, чтобы клиент сразу увидел контекст.
        for entry in log_buffer.snapshot(100):
            await response.write(b"data: " + json.dumps(entry).encode() + b"\n\n")

        queue = log_buffer.subscribe()
        try:
            while True:
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                    await response.write(b"data: " + json.dumps(entry).encode() + b"\n\n")
                except asyncio.TimeoutError:
                    # heartbeat-комментарий, чтобы прокси не разрывали connection
                    await response.write(b": keepalive\n\n")
        except (asyncio.CancelledError, ConnectionResetError):
            pass
        finally:
            log_buffer.unsubscribe(queue)
        return response

    # ----------------------------- catalog & auth endpoints ---------------

    UNLOCK_COOKIE = "max_unlock"

    def _is_request_unlocked(self, request: web.Request) -> bool:
        if not self.config_store.data.dangerous_password_hash:
            return True
        token = request.cookies.get(self.UNLOCK_COOKIE)
        return session_manager.is_valid(token)

    async def auth_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "password_configured": bool(self.config_store.data.dangerous_password_hash),
            "unlocked": self._is_request_unlocked(request),
            "active_sessions": session_manager.active_count(),
        })

    async def auth_unlock(self, request: web.Request) -> web.Response:
        data = await request.post()
        password = (data.get("password") or "").strip()
        if not self.config_store.data.dangerous_password_hash:
            return web.json_response({"ok": True, "reason": "no-password-configured"})
        if not password:
            return web.json_response({"ok": False, "reason": "empty"}, status=400)
        ok = verify_password(
            password,
            self.config_store.data.dangerous_password_hash,
            self.config_store.data.dangerous_password_salt,
        )
        if not ok:
            logger.warning("Web UI unlock: неверный пароль")
            return web.json_response({"ok": False, "reason": "invalid"}, status=401)
        session = session_manager.create(label="webui")
        response = web.json_response({"ok": True, "expires_in": session_manager.ttl})
        response.set_cookie(
            self.UNLOCK_COOKIE,
            session.token,
            max_age=session_manager.ttl,
            httponly=True,
            samesite="Strict",
        )
        logger.info("Web UI unlock: сессия открыта")
        return response

    async def auth_lock(self, request: web.Request) -> web.Response:
        token = request.cookies.get(self.UNLOCK_COOKIE)
        session_manager.revoke(token)
        response = web.json_response({"ok": True})
        response.del_cookie(self.UNLOCK_COOKIE)
        return response

    async def catalog_endpoint(self, _: web.Request) -> web.Response:
        catalog = load_catalog()
        return web.json_response({
            "version": catalog.version,
            "source": catalog.source,
            "modules": annotate_installed(catalog, MODULES_DIR),
        })

    async def catalog_install(self, request: web.Request) -> web.Response:
        if not self._is_request_unlocked(request):
            return web.json_response({"ok": False, "reason": "locked"}, status=403)
        data = await request.post()
        name = (data.get("name") or "").strip()
        if not name:
            return web.json_response({"ok": False, "reason": "name required"}, status=400)
        catalog = load_catalog()
        entry: CatalogEntry | None = next(
            (m for m in catalog.modules if m.name.lower() == name.lower()),
            None,
        )
        if entry is None:
            return web.json_response({"ok": False, "reason": "not-in-catalog"}, status=404)
        result = install_module(entry, MODULES_DIR)
        ok = result.status in {"installed", "up_to_date"}
        body = {
            "ok": ok,
            "status": result.status,
            "bytes_written": result.bytes_written,
            "name": entry.name,
            "filename": entry.filename,
        }
        if not ok:
            body["error"] = result.error
        if ok:
            logger.info("Каталог: установлен %s (%s)", entry.name, result.status)
        return web.json_response(body)

    async def catalog_uninstall(self, request: web.Request) -> web.Response:
        if not self._is_request_unlocked(request):
            return web.json_response({"ok": False, "reason": "locked"}, status=403)
        data = await request.post()
        name = (data.get("name") or "").strip()
        catalog = load_catalog()
        entry = next(
            (m for m in catalog.modules if m.name.lower() == name.lower()),
            None,
        )
        filename = entry.filename if entry else (data.get("filename") or "").strip()
        result = uninstall_module(filename, MODULES_DIR)
        ok = result.status == "installed"
        body = {"ok": ok, "status": result.status, "filename": filename}
        if not ok:
            body["error"] = result.error
        if ok:
            logger.info("Каталог: удалён %s", filename)
        return web.json_response(body)

    async def start(self) -> str:
        if self.runner is not None:
            return f"http://{self.host}:{self.port}"
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/health", self.health)
        app.router.add_get("/api/stats", self.stats_endpoint)
        app.router.add_get("/api/logs", self.logs_history)
        app.router.add_get("/api/logs/stream", self.logs_stream)
        app.router.add_get("/api/auth/status", self.auth_status)
        app.router.add_post("/api/auth/unlock", self.auth_unlock)
        app.router.add_post("/api/auth/lock", self.auth_lock)
        app.router.add_get("/api/catalog", self.catalog_endpoint)
        app.router.add_post("/api/catalog/install", self.catalog_install)
        app.router.add_post("/api/catalog/uninstall", self.catalog_uninstall)
        app.router.add_post("/api/accounts", self.add_account)
        app.router.add_post("/api/config", self.update_config)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        return f"http://{self.host}:{self.port}"

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()
            self.runner = None
            self.site = None


# -------------------------------- globals ------------------------------------
config_store = ConfigStore(CONFIG_FILE)
config_store.load()
account_store = AccountStore(ACCOUNTS_FILE)
module_registry = ModuleRegistry()
module_registry.preload_default_modules()
webui = WebUIManager(module_registry, config_store, account_store)
weather_client = WeatherClient()


def normalize_command(text: str) -> str:
    # поддерживаем оба префикса, но стандартный — точка
    if text.startswith("!"):
        return f".{text[1:]}"
    return text


def extract_reply_py(packet: dict) -> str | None:
    message = packet.get("payload", {}).get("message", {})
    reply = message.get("replyMessage") or message.get("reply") or {}
    attaches = reply.get("attaches") or reply.get("attachments") or []
    for item in attaches:
        file_path = item.get("path") or item.get("filePath")
        if file_path and str(file_path).endswith(".py"):
            return str(file_path)
    return None




async def download_module_from_url(url: str) -> Path:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Нужна ссылка http/https")

    MODULES_DIR.mkdir(exist_ok=True)
    filename = url.rstrip('/').split('/')[-1] or 'downloaded_module.py'
    if not filename.endswith('.py'):
        filename += '.py'

    safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    destination = MODULES_DIR / safe_name

    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            content = await response.read()

    destination.write_bytes(content)
    return destination

def resolve_destination_chat(payload: dict, source_chat_id: int) -> int:
    """Guard against accidental random destination changes while processing."""
    if config_store.data.random_reroute_guard:
        return source_chat_id
    return int(payload.get("chatId", source_chat_id))


async def try_login(client: MaxClient) -> None:
    if SESSION_FILE.exists():
        raw = SESSION_FILE.read_text(encoding="utf-8").strip()
        if "\n" in raw:
            device_id, token = raw.split("\n", maxsplit=1)
            try:
                await client.login_by_token(token, device_id)
                logger.info("Login by token successful")
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("Token login failed: %s", exc)

    phone = os.getenv("MAX_PHONE") or input("Enter phone number (+79990000000): ").strip()
    sms_token = await client.send_code(phone)
    sms_code = int(os.getenv("MAX_SMS_CODE") or input("Enter SMS code: ").strip())
    account_data = await client.sign_in(sms_token, sms_code)
    token = account_data["payload"]["tokenAttrs"]["LOGIN"]["token"]
    SESSION_FILE.write_text(f"{client.device_id}\n{token}", encoding="utf-8")
    account_store.add_or_update(AccountEntry(label="main", phone=phone, state="authorized", device_id=client.device_id, token=token))


_TG_UNLOCKED: dict[str, float] = {}


def _tg_session_active() -> bool:
    """Активна ли в Telegram-канале unlock-сессия (одна на бот)."""
    expires = _TG_UNLOCKED.get("default")
    if expires is None:
        return False
    if expires < time.time():
        _TG_UNLOCKED.pop("default", None)
        return False
    return True


async def _ensure_unlocked(client: MaxClient, dst_chat: int, message_id: int, cmd: str) -> bool:
    """Проверяет, можно ли выполнять опасную команду. Если нет — отвечает в чат."""
    if cmd == "unlock":
        return True
    if not config_store.data.dangerous_password_hash:
        # Пароль не задан — пропускаем (например, бот пока запущен впервые без сетапа).
        return True
    if _tg_session_active():
        return True
    await edit_message(
        client,
        dst_chat,
        message_id,
        "🔒 Команда требует unlock. Сначала выполните <code>.unlock &lt;пароль&gt;</code> "
        "(сессия активна 10 минут).",
    )
    return False


async def process_builtin(client: MaxClient, packet: dict, chat_id: int, message_id: int, cmd: str, arg: str) -> bool:
    api = MaxApiExtensions(client)
    ctx = BotContext(client=client, registry=module_registry, api=api, config=config_store)
    destination_chat = resolve_destination_chat(packet.get("payload", {}), chat_id)

    # ---- dangerous-команды требуют активную unlock-сессию ----
    if is_dangerous(cmd):
        ok = await _ensure_unlocked(client, destination_chat, message_id, cmd)
        if not ok:
            return True

    if cmd == "unlock":
        if not config_store.data.dangerous_password_hash:
            await edit_message(client, destination_chat, message_id, "Пароль не задан — unlock не нужен.")
            return True
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: <code>.unlock &lt;пароль&gt;</code>")
            return True
        if verify_password(arg, config_store.data.dangerous_password_hash, config_store.data.dangerous_password_salt):
            _TG_UNLOCKED["default"] = time.time() + 600
            await edit_message(client, destination_chat, message_id, "🔓 Сессия открыта на 10 минут.")
            logger.info("Dangerous-actions сессия открыта (Telegram).")
        else:
            await edit_message(client, destination_chat, message_id, "❌ Неверный пароль.")
            logger.warning("Неверный пароль .unlock от Telegram.")
        return True

    if cmd == "lock":
        _TG_UNLOCKED.pop("default", None)
        await edit_message(client, destination_chat, message_id, "🔒 Сессия закрыта.")
        return True

    if cmd == "catalog":
        catalog = load_catalog()
        rows = annotate_installed(catalog, MODULES_DIR)
        if not rows:
            await edit_message(client, destination_chat, message_id, "Каталог пуст.")
            return True
        lines = [f"<b>Каталог модулей</b> ({catalog.source or 'локальный'})"]
        for r in rows:
            mark = "✅" if r["installed"] else "⬜"
            lines.append(
                f"{mark} <b>{html.escape(r['name'])}</b> v{html.escape(r['version'])} — "
                f"{html.escape(r['description'])}"
            )
        lines.append("\nУстановить: <code>.installmod &lt;name&gt;</code>")
        await edit_message(client, destination_chat, message_id, "\n".join(lines))
        return True

    if cmd == "installmod":
        target_name = arg.strip()
        if not target_name:
            await edit_message(client, destination_chat, message_id, "Использование: <code>.installmod &lt;name&gt;</code>")
            return True
        catalog = load_catalog()
        entry = next((m for m in catalog.modules if m.name.lower() == target_name.lower()), None)
        if not entry:
            await edit_message(client, destination_chat, message_id, f"Модуль <code>{html.escape(target_name)}</code> не найден в каталоге.")
            return True
        result = install_module(entry, MODULES_DIR)
        if result.status == "installed":
            await edit_message(client, destination_chat, message_id, f"📦 {html.escape(entry.name)} установлен ({result.bytes_written} B). Перезапустите бот для активации.")
        elif result.status == "up_to_date":
            await edit_message(client, destination_chat, message_id, f"📦 {html.escape(entry.name)} — уже установлен (актуальная версия).")
        else:
            await edit_message(client, destination_chat, message_id, f"❌ Ошибка установки: <code>{html.escape(result.error)}</code>")
        return True

    if cmd == "uninstallmod":
        catalog = load_catalog()
        entry = next((m for m in catalog.modules if m.name.lower() == arg.strip().lower()), None)
        filename = entry.filename if entry else arg.strip()
        result = uninstall_module(filename, MODULES_DIR)
        if result.status == "installed":
            await edit_message(client, destination_chat, message_id, f"🗑 {html.escape(filename)} удалён.")
        else:
            await edit_message(client, destination_chat, message_id, f"❌ {html.escape(result.error)}")
        return True

    if cmd in {"modules", "ml"}:
        await edit_message(client, destination_chat, message_id, module_registry.render_modules())
        return True

    if cmd == "ping":
        loop_start = time.perf_counter()
        await asyncio.sleep(0)
        loop_ms = (time.perf_counter() - loop_start) * 1000
        uptime = int(time.time()) - START_TS
        await edit_message(
            client,
            destination_chat,
            message_id,
            f"🏓 pong\nLoop latency: <code>{loop_ms:.2f} ms</code>\nUptime: <code>{uptime}s</code>",
        )
        return True

    if cmd == "help":
        module = module_registry.get_module(arg) if arg else None
        if module:
            details = [f"<b>{module.name}</b>", module.description, ""]
            for item in module.commands:
                alias = f" ({', '.join(item.aliases)})" if item.aliases else ""
                details.append(f"▫️ .{item.name}{alias} - {item.description}")
            details.append("\n☝️ Это встроенный модуль. Вы не можете его выгрузить или заменить")
            await edit_message(client, destination_chat, message_id, to_html("\n".join(details)))
        else:
            await edit_message(client, destination_chat, message_id, "<b>Help:</b> .modules, .weburl, .loadmod, .config, .fconfig, .react")
        return True

    if cmd == "helphide":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .helphide <module>")
            return True
        hidden = module_registry.toggle_hidden(arg)
        if hidden is None:
            await edit_message(client, destination_chat, message_id, "Модуль не найден")
            return True
        await edit_message(client, destination_chat, message_id, f"Модуль {html.escape(arg)} {'скрыт' if hidden else 'показан'}")
        return True

    if cmd == "setprefix":
        new_prefix = arg.strip()
        if not new_prefix:
            await edit_message(client, destination_chat, message_id, f"Текущий префикс: <code>{html.escape(config_store.data.prefix)}</code>")
            return True
        config_store.data.prefix = new_prefix[0]
        config_store.save()
        await edit_message(client, destination_chat, message_id, f"Префикс обновлён: <code>{html.escape(config_store.data.prefix)}</code>")
        return True

    if cmd == "config":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .config <module>")
            return True
        conf = module_registry.module_config(config_store.data, arg)
        config_store.save()
        text = json.dumps(conf, ensure_ascii=False, indent=2)
        await edit_message(client, destination_chat, message_id, f"<b>{html.escape(arg)} config</b>\n<code>{html.escape(text)}</code>")
        return True

    if cmd == "fconfig":
        m = re.match(r"(\S+)\s+(\S+)\s+(.+)", arg)
        if not m:
            await edit_message(client, destination_chat, message_id, "Использование: .fconfig <module> <key> <value>")
            return True
        module_name, key, value = m.groups()
        module_registry.module_config(config_store.data, module_name)[key] = value
        config_store.save()
        await edit_message(client, destination_chat, message_id, "Конфиг обновлён")
        return True

    if cmd == "weburl":
        url = await webui.start()
        await edit_message(client, destination_chat, message_id, f"Web UI: <code>{html.escape(url)}</code>")
        return True

    if cmd == "addacc":
        await edit_message(client, destination_chat, message_id, "Добавление аккаунтов доступно в Web UI (.weburl)")
        return True

    if cmd == "accounts":
        accounts = account_store.load()
        if not accounts:
            await edit_message(client, destination_chat, message_id, "Нет аккаунтов")
            return True
        lines = [f"• {a.label}: {a.phone} ({a.state})" for a in accounts]
        await edit_message(client, destination_chat, message_id, to_html("\n".join(lines)))
        return True

    if cmd == "react":
        # !react <message_id> <emoji>
        m = re.match(r"(\S+)\s+(.+)", arg)
        if not m:
            await edit_message(client, destination_chat, message_id, "Использование: .react <message_id> <emoji>")
            return True
        target_message_id, emoji = m.groups()
        await api.react(destination_chat, target_message_id, emoji.strip())
        await edit_message(client, destination_chat, message_id, "Реакция отправлена")
        return True

    if cmd == "setname":
        # !setname <first> [last]
        parts = arg.split(maxsplit=1)
        if not parts:
            await edit_message(client, destination_chat, message_id, "Использование: .setname <first> [last]")
            return True
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
        await api.update_profile(first_name=first, last_name=last)
        await edit_message(client, destination_chat, message_id, "Имя/фамилия обновлены")
        return True

    if cmd == "setbio":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .setbio <bio>")
            return True
        await api.update_profile(bio=arg)
        await edit_message(client, destination_chat, message_id, "Био обновлено")
        return True

    if cmd == "setfav":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .setfav <chat_id>")
            return True
        config_store.data.favorites_chat_id = int(arg)
        config_store.save()
        await edit_message(client, destination_chat, message_id, "Чат избранного сохранён")
        return True

    if cmd == "favsay":
        if config_store.data.favorites_chat_id is None:
            await edit_message(client, destination_chat, message_id, "Сначала: .setfav <chat_id>")
            return True
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .favsay <text>")
            return True
        await send_message(client, int(config_store.data.favorites_chat_id), arg)
        await edit_message(client, destination_chat, message_id, "Отправлено в избранное")
        return True

    if cmd in {"loadmod", "lm"}:
        path = arg.strip() or extract_reply_py(packet)
        if not path:
            await edit_message(client, destination_chat, message_id, ".loadmod modules/<file.py> или reply на .py")
            return True
        result = await module_registry.load_external_module(path)
        await edit_message(client, destination_chat, message_id, html.escape(result))
        return True

    if cmd in {"dlm", "dlmod"}:
        url = arg.strip()
        if not url:
            await edit_message(client, destination_chat, message_id, "Использование: .dlm <https://.../module.py>")
            return True

        await edit_message(client, destination_chat, message_id, "Скачиваю модуль...")
        module_path = await download_module_from_url(url)
        result = await module_registry.load_external_module(str(module_path))
        await edit_message(client, destination_chat, message_id, f"{html.escape(result)}\nФайл: <code>{html.escape(str(module_path))}</code>")
        return True

    if cmd in {"unloadmod", "ulm"}:
        ok, result = module_registry.try_unload(arg.strip())
        await edit_message(client, destination_chat, message_id, result if ok else f"⚠️ {result}")
        return True

    if cmd == "tr":
        # minimal translator stub
        await edit_message(client, destination_chat, message_id, "Translator module: подключи внешний API перевода")
        return True

    if cmd == "weather":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .weather <город>")
            return True
        weather = await weather_client.get(arg)
        await edit_message(client, destination_chat, message_id, to_html(weather))
        return True

    if cmd == "say":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .say <текст>")
            return True
        await send_message(client, destination_chat, arg)
        return True

    if cmd == "md":
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .md <text>")
            return True
        rendered = safe_markdown(arg) if config_store.data.markdown_enabled else arg
        await edit_message(client, destination_chat, message_id, to_html(rendered))
        return True

    if cmd == "randomsend":
        # debug/demo function for safe destination routing
        if not arg:
            await edit_message(client, destination_chat, message_id, "Использование: .randomsend <текст>")
            return True
        chats = [destination_chat]
        if config_store.data.favorites_chat_id is not None:
            chats.append(int(config_store.data.favorites_chat_id))
        chosen = random.choice(chats)
        await send_message(client, chosen, arg)
        await edit_message(client, destination_chat, message_id, f"Отправлено в chat_id={chosen}")
        return True

    # Сначала пробуем class-based (Hikka-style) команды.
    klass = module_registry.class_commands.get(cmd)
    if klass:
        from core import loader as core_loader
        from core.message import MaxMessage

        message_obj = MaxMessage(client, packet, registry=module_registry)
        await core_loader.dispatch_command(klass, message_obj)
        return True

    dyn = module_registry.dynamic_commands.get(cmd)
    if dyn:
        result = await dyn(ctx, destination_chat, message_id, arg)
        await edit_message(client, destination_chat, message_id, result)
        return True

    return False


async def on_packet(client: MaxClient, packet: dict) -> None:
    stats.packets_in += 1
    for watcher in module_registry.packet_watchers:
        try:
            await watcher(client, packet)
        except Exception:
            logger.exception("Watcher failed")

    if packet.get("opcode") != 128:
        return

    payload = packet.get("payload", {})
    message = payload.get("message", {})
    text = normalize_command((message.get("text") or "").strip())
    prefix = config_store.data.prefix
    if not text.startswith(prefix):
        return

    chat_id = payload.get("chatId")
    message_id = message.get("id")
    if chat_id is None or message_id is None:
        return

    body = text[len(prefix):].strip()
    if not body:
        return

    cmd, *rest = body.split(maxsplit=1)
    arg = rest[0] if rest else ""

    try:
        handled = await process_builtin(client, packet, int(chat_id), int(message_id), cmd.lower(), arg)
        stats.commands_handled += 1
        stats.last_command_ts = time.time()
        if not handled:
            await edit_message(client, int(chat_id), int(message_id), f"Неизвестная команда: <code>{html.escape(cmd)}</code>")
    except Exception as exc:  # noqa: BLE001
        stats.last_error_ts = time.time()
        stats.last_error_msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Command processing failed")
        await edit_message(client, int(chat_id), int(message_id), f"Ошибка: <code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))}</code>")


async def main() -> None:
    MODULES_DIR.mkdir(exist_ok=True)

    client = MaxClient()
    await client.connect()
    await try_login(client)
    await client.set_callback(on_packet)

    logger.info("MAX Userbot started")
    try:
        await asyncio.Future()
    finally:
        await webui.stop()
        await weather_client.close()


if __name__ == "__main__":
    asyncio.run(main())
