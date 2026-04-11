# Module system overview

MaxUB поддерживает два слоя модулей:

1. **Legacy registry** (в `userbot.py`) для обратной совместимости.
2. **Class-based framework** (`core.loader`) — целевая архитектура.

## Class-based style (recommended)

- Наследование от `loader.Module`
- Декоратор `@loader.tds`
- Команды через `@loader.command(...)`
- Конфиги через `loader.ModuleConfig`

См. подробности в: `mods/modules.md`.

## Где лежат модули

- `core_modules/` — встроенные class-based модули
- `modules/` — внешние/кастомные модули

## Загрузка

- `.loadmod modules/name.py`
- `.dlm https://host/module.py`
