"""Системный модуль «Каталог модулей».

Регистрирует декларативный модуль для рендера в Web UI и в выводе `.modules`.
Сами команды (.catalog/.installmod/.uninstallmod/.unlock/.lock) встроены
в `userbot.process_builtin`, поэтому здесь только метаданные.
"""

from userbot import BotModule, ModuleCommand


def setup(registry):
    registry.register_module(
        BotModule(
            name="Catalog",
            description=(
                "Каталог модулей: просмотр и установка по имени. "
                "Команды требуют unlock-сессию (см. .unlock)."
            ),
            commands=[
                ModuleCommand(
                    name="catalog",
                    description="Показать список модулей в каталоге",
                    aliases=["каталог"],
                ),
                ModuleCommand(
                    name="installmod",
                    description="Установить модуль по имени .installmod <name>",
                    aliases=["устmod"],
                ),
                ModuleCommand(
                    name="uninstallmod",
                    description="Удалить модуль по имени .uninstallmod <name>",
                    aliases=["удалитьmod"],
                ),
                ModuleCommand(
                    name="unlock",
                    description="Открыть сессию для опасных действий .unlock <password>",
                ),
                ModuleCommand(
                    name="lock",
                    description="Закрыть сессию опасных действий",
                ),
                ModuleCommand(
                    name="threats",
                    description="Сканировать modules/ на опасные паттерны",
                    aliases=["scanmod"],
                ),
            ],
        )
    )
