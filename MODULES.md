# Module system

В Max-Userbot **два** API для модулей:

| API                       | Когда использовать                                          | Как пишется                          |
|---------------------------|-------------------------------------------------------------|--------------------------------------|
| Class-based (Hikka-style) | Все новые модули, особенно сложные                          | `class X(loader.Module)` + декораторы |
| Legacy `setup(registry)`  | Совсем простые плагины, миграция кода из ранних версий бота | Функция `setup(registry)` в `.py`-файле |

Оба варианта поддерживаются одновременно, можно смешивать в одном проекте.

---

## Class-based API (рекомендуемый)

Совместим с Heroku/Hikka — модули с тех платформ переносятся минимальной правкой
сетевого слоя (Telethon → MaxClient).

### Каркас модуля

```python
from core import loader, utils


@loader.tds  # маркер; no-op в Max, оставлен для совместимости с Hikka
class MyModule(loader.Module):
    """Краткое описание модуля (показывается в .modules / .help)."""

    strings = {
        "name": "MyModule",
        "hi": "👋 Hi, {name}!",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "default_name", "world",
                "Default greeting name.",
                validator=loader.validators.String(min_len=1, max_len=64),
            ),
            loader.ConfigValue(
                "shouty", False,
                "ALL CAPS mode.",
                validator=loader.validators.Boolean(),
            ),
        )

    async def client_ready(self, client, db):
        self.client = client
        self.db = db

    async def on_unload(self):
        # сюда — закрытие http-клиентов, тасков и т.д.
        pass

    @loader.command(ru_doc="[имя] - поздороваться", aliases=["hi"])
    async def hello(self, message):
        name = utils.get_args_raw(message) or self.config["default_name"]
        text = self.strings["hi"].format(name=name)
        if self.config["shouty"]:
            text = text.upper()
        await utils.answer(message, text)

    @loader.watcher(only_incoming=True, ignore_edited=True)
    async def watcher(self, message):
        if "ping" in message.text.lower():
            await message.reply("pong")
```

### Декораторы

| Декоратор              | Что делает                                                                                          |
|------------------------|-----------------------------------------------------------------------------------------------------|
| `@loader.command(...)` | Делает метод командой. Имя команды = имя метода (или `name="..."`). Аргументы: `aliases=[...]`, `ru_doc=...`, `en_doc=...`. |
| `@loader.watcher(...)` | Метод вызывается на каждом подходящем пакете. Фильтры: `only_incoming`, `only_messages`, `ignore_edited`. |
| `@loader.tds`          | Маркер «translatable strings» (для совместимости). В Max — no-op.                                   |
| `@loader.unrestricted` | Маркер «команда не требует расширенных прав» — оставлен на будущее.                                 |

### Конфиг и валидаторы

`loader.ModuleConfig(*loader.ConfigValue(...))` ведёт себя как `dict`, но
проверяет тип/диапазон при записи:

```python
self.config["temperature"] = "1.5"   # ок, приведётся к float
self.config["temperature"] = "five"  # ValueError
self.config["totally_unknown"] = 1   # KeyError
```

Доступные валидаторы (`loader.validators.*`):

- `Boolean()`
- `Integer(minimum=None, maximum=None)`
- `Float(minimum=None, maximum=None)`
- `String(min_len=0, max_len=None)`
- `Hidden(...)` — то же, что `String`, но в Web UI значение скрывается.
- `Choice([...])`
- `RegExp(pattern)`

### Хранилище (DB)

Модулю доступны три метода через `self`:

```python
self.set("key", value)             # запись
value = self.get("key", default)   # чтение
old   = self.pop("key", default)   # удаление
```

Хранилище — это JSON-файл `userbot_db.json`, разделённый на пространства имён по
`strings["name"]`.

### Интерфейс `MaxMessage`

В команды/watcher'ы приходит объект `MaxMessage`:

| Атрибут           | Тип        | Описание                                               |
|-------------------|------------|--------------------------------------------------------|
| `.text`           | `str`      | Текст сообщения.                                       |
| `.id`             | `int`      | ID сообщения.                                          |
| `.chat_id`        | `int`      | ID чата.                                               |
| `.sender_id`      | `int?`     | ID отправителя.                                        |
| `.is_outgoing`    | `bool`     | Отправлено самим пользователем.                        |
| `.is_edited`      | `bool`     | Это редактирование.                                    |
| `.opcode`         | `int`      | Сырой opcode пакета.                                   |
| `.raw`            | `dict`     | Оригинальный пакет.                                    |
| `await .edit(t)`  |            | Отредактировать текущее сообщение.                     |
| `await .reply(t)` |            | Отправить новое сообщение в этот чат.                  |
| `await .answer(t)`|            | Алиас `.edit`, для совместимости с Hikka utils.       |

### `core.utils`

Аналог `hikka/heroku.utils`:

- `utils.get_args_raw(message)` — всё, что после команды строкой.
- `utils.get_args(message)` — то же, но `split()`.
- `utils.get_chat_id(message)` / `utils.get_message_id(message)`.
- `utils.escape_html(text)`.
- `await utils.answer(message, text)` — алиас `message.edit`, возвращает `message`.

### Размещение и автозагрузка

| Каталог             | Когда сканируется                          |
|---------------------|--------------------------------------------|
| `core_modules/`     | Авто, при старте                           |
| `modules/`          | Авто, при старте, рекурсивно (включая `modules/examples/`) |
| внешние URL         | Через команду `.dlmod <url>` / `.loadmod <path>` |

Loader **сам** находит все subclass-ы `loader.Module` в импортируемом файле,
инстанциирует их, вызывает `client_ready(client, db)` и регистрирует команды/
watcher'ы в общем `ModuleRegistry`.

### Готовые примеры

- `modules/examples/gemini_example.py` — упрощённый клиент Google Gemini
  (демонстрирует `Hidden`/`String`/`Float` валидаторы, async HTTP-вызовы).
- `modules/examples/keyscanner_example.py` — авто-ловля API-ключей через
  watcher (per-module DB, `@loader.watcher` фильтры).

---

## Legacy `setup(registry)` API

Минимальный пример (всё ещё работает):

```python
from userbot import BotModule, ModuleCommand


def setup(registry):
    registry.register_module(
        BotModule(
            name="MyModule",
            description="Demo",
            commands=[ModuleCommand(name="mycmd", description="Run demo")],
            builtin=False,
        )
    )
```

Если в `.py`-файле найден и `setup(registry)`, и классы `loader.Module` — будут
использованы оба пути.

### Миграция legacy → class-based

1. Завернуть код в `class X(loader.Module)` с `strings = {"name": "X"}`.
2. Команды — методы с `@loader.command(...)`.
3. `register_dynamic_command(...)` → метод+декоратор.
4. `register_watcher(callback)` → метод с `@loader.watcher(...)`.
5. Хранилище → `self.set/self.get/self.pop`.
6. Любой `BotContext`/`MaxApiExtensions`-вызов остаётся доступен через
   `self.client` (это `MaxClient`).

---

## Конфиг через Web UI и команды

- Команды `.config <module>` / `.fconfig <module> <key> <value>` — работают и для
  class-based, и для legacy-модулей.
- Web UI на `http://127.0.0.1:8088/` — каждое поле конфига выводится отдельной
  формой, с доком и текущим значением.
- Health-check эндпоинт: `GET /health` — JSON со статусом, аптаймом, числом
  модулей и аккаунтов.

## Каталог модулей

Встроенный каталог хранится в `catalog.json` (рядом с `userbot.py`). Опционально
можно задать переменную окружения `MAX_CATALOG_URL` — тогда каталог сначала
будет качаться оттуда (HTTPS-URL JSON в том же формате).

Формат записи:

```json
{
  "name": "Foo",
  "description": "что делает",
  "version": "1.0",
  "author": "you",
  "filename": "foo.py",
  "url": "https://raw.githubusercontent.com/.../foo.py",
  "tags": ["category"]
}
```

Команды (системный модуль `Catalog`):

- `.catalog` — показать все записи каталога с пометкой «установлен/нет».
- `.installmod <name>` — скачать `url` и положить в `modules/<filename>`. Размер
  файла лимитирован 1 МБ. Если контент совпадает с уже установленным — возвращает
  `up_to_date`.
- `.uninstallmod <name>` — удалить файл модуля.

В Web UI: секция «Каталог модулей» с MD3-карточками, кнопками Install/Uninstall
и тегами. Все эти действия — опасные, требуют unlock (см. ниже).

API эндпоинты:

- `GET /api/catalog` → `{version, source, modules: [...] }` с полем `installed`.
- `POST /api/catalog/install` (form: `name`) → требует unlock-cookie, иначе 403.
- `POST /api/catalog/uninstall` (form: `name`) → то же.

## Пароль для опасных действий

При первом запуске `python main.py` спросит интерактивно через `getpass`:

```
=========================================================
  Установите пароль для опасных действий
  (eval/terminal/.dlm/install/uninstall/addaccount).
=========================================================
Пароль:
Повторите пароль:
```

Хеш — `hashlib.scrypt` (stdlib, n=16384, r=8, p=1) — сохраняется в
`userbot_config.json` (поля `dangerous_password_hash` и `dangerous_password_salt`).
Пустой ввод пропускает установку — тогда бот работает как раньше, без проверок.
В неинтерактивном режиме (systemd, docker без TTY) можно задать
`MAX_DANGEROUS_PASSWORD=...` через окружение — будет однократно прохэширован
и сохранён в конфиг.

### Опасные команды

`eval`, `exec`, `terminal`, `shell`, `sh`, `dlm`, `loadmod`, `installmod`,
`uninstallmod`, `rmmod`, `addaccount`, `loginacc`, `deleteaccount`,
`delaccount`, `removeaccount` — требуют активную unlock-сессию.

В Telegram: `.unlock <пароль>` открывает сессию на 10 минут (по умолчанию;
конфигурится `MAX_UNLOCK_TTL` в секундах). `.lock` — закрывает. После того как
сессия истекла, дальнейшие опасные команды отвечают:

```
🔒 Команда требует unlock. Сначала выполните .unlock <пароль>
```

В Web UI: иконка-замок в app bar — клик по ней при закрытой сессии открывает
модал с паролем; после успеха иконка становится `lock_open`. Любая опасная
кнопка (Install / Uninstall / Add account) при закрытой сессии автоматически
открывает этот же модал — после ввода пароля действие повторяется.

Endpoints:

- `GET /api/auth/status` → `{password_configured, unlocked, active_sessions}`.
- `POST /api/auth/unlock` (form: `password`) → ставит httpOnly-cookie `max_unlock`.
- `POST /api/auth/lock` → удаляет cookie и отзывает токен.
