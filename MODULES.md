# Module system (draft)

## Текущее состояние

- Core registry со встроенными модулями (`builtin=True`): их нельзя выгружать/заменять.
- Поддержка динамических команд: `register_dynamic_command`.
- Поддержка watcher-ов пакетов: `register_watcher`.
- Поддержка отправки opcode из модулей через `BotContext.send_opcode`.
- Конфиги модулей доступны:
  - через сообщения `!config / !fconfig`,
  - через Web UI форму `/api/config`.
- Загрузка внешних модулей из `./modules`:
  - `!loadmod modules/<name>.py`
  - или reply на `.py` файл.

## Минимальный API плагина

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

## Примеры

- `modules/examples/echo_plugin.py`
- `modules/examples/opcode_watcher_plugin.py`
