# 📚 Документация по созданию модулей для Maxli UserBot

> **Maxli** использует библиотеку [PyMax (maxapi-python)](https://fresh-milkshake.github.io/pymax/) — Python-обёртку для Max Messenger API.
>
> 📖 Полная документация PyMax: https://fresh-milkshake.github.io/pymax/

## 📋 Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Структура модуля](#структура-модуля)
3. [Maxli API](#maxli-api)
4. [Прямой доступ к PyMax](#прямой-доступ-к-pymax)
5. [Форматирование текста](#форматирование-текста)
6. [Работа с файлами и медиа](#работа-с-файлами-и-медиа)
7. [Конфигурация модулей](#конфигурация-модулей)
8. [Продвинутые возможности](#продвинутые-возможности)
9. [Примеры модулей](#примеры-модулей)

---

## Быстрый старт

### Минимальный модуль

```python
# name: Мой модуль
# version: 1.0.0
# developer: Ваше имя
# id: my_module
# min-maxli: 35

async def hello_command(api, message, args):
    await api.edit(message, "👋 **Привет!**", markdown=True)

async def register(api):
    api.register_command("hello", hello_command)
```

### Загрузка модуля

1. Создайте файл `.py` с кодом модуля
2. Отправьте файл боту с командой `.loadmod` или `.dlm <url>`
3. Или положите файл в папку `modules/` и перезапустите бота
4. Используйте команду с префиксом (например `.hello`)

---

## Структура модуля

### Обязательные метаданные

```python
# name: Название модуля
# version: 1.0.0
# developer: Имя разработчика
# id: unique_module_id
# min-maxli: 35
```

| Поле | Описание |
|------|----------|
| `name` | Отображаемое название модуля |
| `version` | Версия модуля (semver) |
| `developer` | Имя разработчика |
| `id` | Уникальный ID |
| `min-maxli` | Минимальная версия Maxli |

### Основные функции

```python
async def register(api):
    api.register_command("cmd", command_handler)
    api.register_watcher(watcher_handler)
```

---

## Maxli API

### Работа с сообщениями

```python
await api.edit(message, "**Жирный**", markdown=True)
await api.reply(message, "Ответ", markdown=True)
await api.delete(message)
```

### Реакции

```python
await api.set_reaction(message, "❤️")
```

---

## Прямой доступ к PyMax

```python
client = api.client
await client.send_message(chat_id=123, text="Привет!")
```

---

## Форматирование текста

Поддерживается markdown и программный парсинг элементов форматирования.

---

## Работа с файлами и медиа

```python
await api.send_file(chat_id=chat_id, file_path="file.txt", text="Описание")
await api.send_photo(chat_id=chat_id, file_path="image.jpg")
```

---

## Конфигурация модулей

```python
register_module_settings("my_module", {"enabled": {"default": True}})
value = get_module_setting("my_module", "enabled", True)
```

---

## Продвинутые возможности

- Watchers
- Async задачи через `asyncio.gather`
- Низкоуровневые opcode-операции через `api.client`

---

## Примеры модулей

См. `modules/examples/echo_plugin.py` и `modules/examples/opcode_watcher_plugin.py`.

---

## Полезные ссылки

- PyMax docs: https://fresh-milkshake.github.io/pymax/
- PyMax package: `pip install maxapi-python`
