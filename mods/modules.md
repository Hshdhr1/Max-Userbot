# 📋 Ультимативный гайд: создание модулей MaxUB 2.0 (vkmax)

> **Контекст:** MaxUB использует `vkmax` и собственный класс-ориентированный модульный фреймворк (`core.loader`).
> Здесь описан стиль, похожий на Heroku/Hikka-подход, но адаптированный под MAX и без Telethon-inline.

---

## 1) Технический стек

- **Python:** 3.10+
- **MAX API client:** `vkmax`
- **Сетевые запросы:** только `aiohttp`
- **Архитектура модуля:** класс, наследуемый от `loader.Module`, с декоратором `@loader.tds`
- **Команды:** методы с декоратором `@loader.command("name", "описание")`

Базовый импорт:

```python
from core import loader
```

Метаданные в начале файла:

```python
# meta developer: @username
# scope: maxub
# requires: aiohttp vkmax
__version__ = (1, 0, 0)
```

---

## 2) Безопасность и конфиги (строго)

- Никакого хардкода токенов/API-ключей
- Любые секреты — через `loader.ModuleConfig` + `loader.validators.Hidden()`
- Числовые/диапазонные значения — через `loader.validators.Integer(...)`

Пример:

```python
def __init__(self):
    self.config = loader.ModuleConfig(
        loader.ConfigValue(
            "api_key",
            "",
            lambda: "API ключ",
            validator=loader.validators.Hidden(),
        ),
        loader.ConfigValue(
            "limit",
            10,
            lambda: "Размер батча",
            validator=loader.validators.Integer(minimum=1, maximum=100),
        ),
    )
```

Проверка перед внешним API:

```python
if not self.config["api_key"]:
    return "❌ Не указан API ключ"
```

---

## 3) База модуля и команды

```python
from core import loader

@loader.tds
class MyModule(loader.Module):
    strings = {"name": "MyModule"}

    def __init__(self):
        super().__init__()

    @loader.command("hello", "Приветствие")
    async def hello_cmd(self, message_ctx: dict, args_raw: str) -> str:
        return "👋 Привет из MyModule"
```

Где:
- `message_ctx` содержит `chat_id`, `message_id`, `packet`
- `args_raw` — строка аргументов после команды

---

## 4) Асинхронность и производительность

- Все IO только async
- Для массовых задач: `asyncio.Queue` + `asyncio.Semaphore`
- Ошибки ловить и логировать

```python
import asyncio
import logging
logger = logging.getLogger(__name__)

async def worker(queue: asyncio.Queue):
    while True:
        item = await queue.get()
        try:
            ...
        except Exception as e:
            logger.error("Worker error: %s", e)
        finally:
            queue.task_done()
```

---

## 5) Работа с vkmax и низкоуровневым API

Через runtime-контекст можно отправлять opcode напрямую (если модулю это нужно).
Всегда валидируйте входные данные пользователя.

---

## 6) Текст, локализация, экранирование

- Держите тексты в `strings`
- Экранируйте пользовательский ввод перед HTML-выводом
- Для markdown-вывода используйте helper runtime

---

## 7) Жизненный цикл

Опционально реализуйте:

- `client_ready(self, api, db)`
- `on_unload(self)`

Для сетевых ресурсов (aiohttp session, фоновые задачи) обязательно корректное закрытие в `on_unload`.

---

## 8) Загрузка модулей

- Из файла: `.loadmod modules/name.py`
- По ссылке: `.dlm https://host/module.py`
- Через class-based discovery: `core_modules/*.py` и `modules/*.py`

---

## 9) Чеклист качества

- [ ] Есть мета-теги и `__version__`
- [ ] Используется `@loader.tds`
- [ ] Команды объявлены через `@loader.command`
- [ ] Нет sync-запросов (`requests` запрещен)
- [ ] Секреты в `ModuleConfig + Hidden`
- [ ] Ошибки API обрабатываются
- [ ] Код async и не блокирует event loop

---

## 10) Минимальный шаблон (готов к копированию)

```python
# meta developer: @yourname
# scope: maxub
# requires: aiohttp vkmax
__version__ = (1, 0, 0)

from core import loader

@loader.tds
class ExampleModule(loader.Module):
    strings = {
        "name": "ExampleModule",
    }

    @loader.command("example", "Demo command")
    async def example_cmd(self, message_ctx: dict, args_raw: str) -> str:
        return f"✅ example: {args_raw or 'ok'}"
```
