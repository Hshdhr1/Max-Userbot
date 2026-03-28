import asyncio
import html
import importlib.util
import json
import logging
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from aiohttp import web
from vkmax.client import MaxClient
from vkmax.functions.messages import edit_message, send_message

LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%d.%m.%Y %H:%M:%S"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=DATE_FORMAT)
logger = logging.getLogger("max-userbot")

LOG_BUFFER = deque(maxlen=500)


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            LOG_BUFFER.append(self.format(record))
        except Exception:
            pass


_buffer_handler = _BufferHandler()
_buffer_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
logging.getLogger().addHandler(_buffer_handler)

SESSION_FILE = Path("max_session.txt")
MODULES_DIR = Path("modules")
CONFIG_FILE = Path("userbot_config.json")
ACCOUNTS_FILE = Path("accounts.json")
DEFAULT_PREFIX = "."
START_TS = int(time.time())


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


@dataclass
class UserbotConfig:
    prefix: str = DEFAULT_PREFIX
    markdown_enabled: bool = True
    random_reroute_guard: bool = True
    favorites_chat_id: int | None = None
    module_configs: dict[str, dict[str, Any]] = field(default_factory=dict)


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
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = UserbotConfig(**payload)

    def save(self) -> None:
        self.path.write_text(json.dumps(asdict(self.data), ensure_ascii=False, indent=2), encoding="utf-8")


class AccountStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[AccountEntry]:
        if not self.path.exists():
            return []
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return [AccountEntry(**row) for row in rows]

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
        lines = [f"<b>Система модулей</b>", f"{len(self.available_modules)} модулей доступно, {len(self.hidden_modules)} скрыто:", ""]
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
        options_html = "".join(
            f"<div class='cfg-row'><label>{html.escape(k)}</label><input name='value' value='{html.escape(str(v))}'><input type='hidden' name='module' value='{html.escape(module.name)}'><input type='hidden' name='key' value='{html.escape(k)}'><button>Save</button></div>"
            for k, v in module_conf.items()
        ) or "<div class='muted'>Нет конфигов (добавь через сообщения или API)</div>"

        return (
            "<section class='module-card'>"
            f"<h3>{html.escape(module.name)}</h3>"
            f"<p>{html.escape(module.description)}</p>"
            f"<small>{len(module.commands)} commands</small>"
            f"<form method='post' action='/api/config'>{options_html}</form>"
            "</section>"
        )

    async def index(self, _: web.Request) -> web.Response:
        modules_nav = "".join(
            f"<li>{html.escape(m.name)} <span>{len(m.commands)}</span></li>" for m in self.registry.available_modules
        )
        cards = "".join(self._module_panel(m) for m in self.registry.available_modules)

        accounts = self.account_store.load()
        accounts_html = "".join(
            f"<div class='account'><b>{html.escape(a.label)}</b><small>{html.escape(a.phone)}</small><em>{html.escape(a.state)}</em></div>"
            for a in accounts
        ) or "<div class='account muted'>Нет подключённых аккаунтов</div>"

        uptime = int(time.time()) - START_TS
        html_page = f"""
<!doctype html>
<html lang='ru'>
<head>
<meta charset='utf-8'>
<title>Max Userbot Web UI</title>
<style>
:root {{ --bg:#0d0f17; --card:#141826; --line:#252d42; --text:#eef2ff; --muted:#8d97b5; --accent:#16a34a; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:Inter,Arial,sans-serif; }}
.layout {{ display:grid; grid-template-columns:280px 1fr; min-height:100vh; }}
.sidebar {{ border-right:1px solid var(--line); padding:18px; background:#111521; }}
.brand {{ font-size:26px; font-weight:800; margin-bottom:14px; }}
.search {{ width:100%; padding:10px 12px; background:#0f1320; border:1px solid var(--line); border-radius:12px; color:var(--text); }}
.sidebar ul {{ list-style:none; padding:0; margin:14px 0 0; }}
.sidebar li {{ display:flex; justify-content:space-between; padding:10px 8px; border-radius:10px; }}
.sidebar li:hover {{ background:#171d2d; }}
.main {{ padding:22px; }}
.stats {{ display:grid; grid-template-columns:repeat(3,minmax(180px,1fr)); gap:12px; margin-bottom:14px; }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; }}
.stat h2 {{ margin:0; font-size:30px; color:#20d38a; }}
.accounts, .modules {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px; margin-top:12px; }}
.module-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:10px; }}
.module-card {{ background:#101523; border:1px solid var(--line); border-radius:12px; padding:12px; }}
.module-card p {{ color:var(--muted); margin:6px 0; }}
.cfg-row {{ display:flex; gap:8px; margin-top:8px; }}
.cfg-row input {{ flex:1; padding:8px; background:#0f1320; border:1px solid var(--line); color:var(--text); border-radius:8px; }}
button {{ background:#315efb; color:white; border:none; border-radius:8px; padding:8px 12px; cursor:pointer; }}
.muted {{ color:var(--muted); }}
.account {{ display:flex; gap:10px; align-items:center; border:1px solid var(--line); padding:8px 10px; border-radius:10px; margin-top:8px; }}
.account em {{ margin-left:auto; color:#6b7280; }}
.add-account {{ display:grid; grid-template-columns:1fr 1fr auto; gap:8px; }}
.add-account input {{ padding:8px; border-radius:8px; border:1px solid var(--line); background:#0f1320; color:var(--text); }}
</style>
</head>
<body>
<div class='layout'>
  <aside class='sidebar'>
    <div class='brand'>Maxli</div>
    <input class='search' placeholder='Поиск модулей...'>
    <ul>{modules_nav}</ul>
  </aside>
  <main class='main'>
    <section class='stats'>
      <div class='stat'><div>Активные клиенты</div><h2>{max(len(accounts), 1)}</h2></div>
      <div class='stat'><div>Загруженные модули</div><h2>{len(self.registry.available_modules)}</h2></div>
      <div class='stat'><div>Uptime</div><h2>{uptime}s</h2></div>
    </section>

    <section class='accounts'>
      <h2>Подключенные аккаунты</h2>
      <form class='add-account' method='post' action='/api/accounts'>
        <input name='label' placeholder='label (main)'>
        <input name='phone' placeholder='+79990000000'>
        <button type='submit'>Добавить</button>
      </form>
      {accounts_html}
    </section>

    <section class='modules'>
      <h2>Модули / конфиги</h2>
      <div class='module-grid'>{cards}</div>
    </section>

    <section class='modules'>
      <h2>Каталог модулей</h2>
      <ul id='moduleCatalog' class='muted'>Загрузка...</ul>
    </section>

    <section class='modules'>
      <h2>Стрим логов</h2>
      <pre id='logStream' style='max-height:260px;overflow:auto;background:#0b1020;border:1px solid var(--line);padding:10px;border-radius:10px;'></pre>
    </section>
  </main>
</div>
<script>
async function loadCatalog(){{
  try{{
    const res = await fetch('/api/modules/catalog');
    const data = await res.json();
    const ul = document.getElementById('moduleCatalog');
    ul.innerHTML = '';
    data.modules.forEach(m => {{
      const li = document.createElement('li');
      li.innerHTML = `<b>${{m.name}}</b> — ${{m.commands.join(', ')}}`;
      ul.appendChild(li);
    }});
  }}catch(e){{
    document.getElementById('moduleCatalog').innerText = 'Ошибка загрузки каталога';
  }}
}}
function startLogStream(){{
  const box = document.getElementById('logStream');
  const events = new EventSource('/api/logs/stream');
  events.onmessage = (ev) => {{
    box.textContent += ev.data + '\n';
    box.scrollTop = box.scrollHeight;
  }};
}}
loadCatalog();
startLogStream();
</script>
</body>
</html>
"""
        return web.Response(text=html_page, content_type="text/html")

    async def add_account(self, request: web.Request) -> web.Response:
        data = await request.post()
        label = (data.get("label") or "").strip()
        phone = (data.get("phone") or "").strip()
        if not label or not phone:
            return web.Response(status=400, text="label and phone are required")
        self.account_store.add_or_update(AccountEntry(label=label, phone=phone, state="pending_auth"))
        raise web.HTTPFound("/")

    async def update_config(self, request: web.Request) -> web.Response:
        data = await request.post()
        module = (data.get("module") or "").strip().lower()
        key = (data.get("key") or "").strip()
        value = (data.get("value") or "").strip()
        if not module or not key:
            return web.Response(status=400, text="module and key are required")
        self.registry.module_config(self.config_store.data, module)[key] = value
        self.config_store.save()
        raise web.HTTPFound("/")


    async def module_catalog(self, _: web.Request) -> web.Response:
        data = [
            {
                "name": m.name,
                "description": m.description,
                "commands": [c.name for c in m.commands],
                "builtin": m.builtin,
                "hidden": m.hidden,
            }
            for m in self.registry.available_modules
        ]
        return web.json_response({"modules": data})

    async def stream_logs(self, _: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(_)

        last_sent = 0
        try:
            while True:
                lines = list(LOG_BUFFER)
                if last_sent < len(lines):
                    for line in lines[last_sent:]:
                        payload = line.replace("\n", " ")
                        await resp.write(f"data: {payload}\n\n".encode("utf-8"))
                    last_sent = len(lines)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        return resp

    async def start(self) -> str:
        if self.runner is not None:
            return f"http://{self.host}:{self.port}"
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_post("/api/accounts", self.add_account)
        app.router.add_post("/api/config", self.update_config)
        app.router.add_get("/api/modules/catalog", self.module_catalog)
        app.router.add_get("/api/logs/stream", self.stream_logs)
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


async def process_builtin(client: MaxClient, packet: dict, chat_id: int, message_id: int, cmd: str, arg: str) -> bool:
    api = MaxApiExtensions(client)
    ctx = BotContext(client=client, registry=module_registry, api=api, config=config_store)
    destination_chat = resolve_destination_chat(packet.get("payload", {}), chat_id)

    if cmd in {"modules", "ml"}:
        await edit_message(client, destination_chat, message_id, module_registry.render_modules())
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
        if not arg:
            await edit_message(client, destination_chat, message_id, f"Текущий префикс: <code>{html.escape(config_store.data.prefix)}</code>")
            return True
        config_store.data.prefix = arg.strip()[0]
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

    dyn = module_registry.dynamic_commands.get(cmd)
    if dyn:
        result = await dyn(ctx, destination_chat, message_id, arg)
        await edit_message(client, destination_chat, message_id, result)
        return True

    return False


async def on_packet(client: MaxClient, packet: dict) -> None:
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
        if not handled:
            await edit_message(client, int(chat_id), int(message_id), f"Неизвестная команда: <code>{html.escape(cmd)}</code>")
    except Exception as exc:  # noqa: BLE001
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
