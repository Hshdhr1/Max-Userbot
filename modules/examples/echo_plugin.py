"""Пример внешнего плагина: команда !echo."""

from userbot import BotContext, BotModule, ModuleCommand


async def echo_handler(_: BotContext, __: int, ___: int, arg: str) -> str:
    if not arg:
        return "Использование: !echo <текст>"
    return arg


def setup(registry):
    registry.register_module(
        BotModule(
            name="EchoPlugin",
            description="Тестовый модуль echo",
            commands=[ModuleCommand(name="echo", description="Повторяет текст")],
            builtin=False,
        )
    )
    registry.register_dynamic_command("echo", echo_handler)
