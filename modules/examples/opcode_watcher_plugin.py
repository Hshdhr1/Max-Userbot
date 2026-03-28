"""Пример внешнего плагина: watcher входящих opcode + пользовательская команда."""

from userbot import BotContext, BotModule, ModuleCommand


def setup(registry):
    async def watcher(_, packet):
        # можно логировать пакеты, фильтровать opcode, делать аналитику и т.д.
        if packet.get("opcode") == 128:
            return

    async def rawsend_handler(ctx: BotContext, _chat_id: int, _message_id: int, arg: str) -> str:
        """Формат: !rawsend <opcode> <json_payload>."""
        import json

        if not arg:
            return "Использование: !rawsend <opcode> <json_payload>"

        opcode_raw, payload_raw = arg.split(maxsplit=1)
        opcode = int(opcode_raw)
        payload = json.loads(payload_raw)
        response = await ctx.send_opcode(opcode=opcode, payload=payload)
        return f"Отправлено. Ответ: {response}"

    registry.register_module(
        BotModule(
            name="OpcodeTools",
            description="Тестовый модуль: watcher и отправка opcode",
            commands=[ModuleCommand(name="rawsend", description="Отправить кастомный opcode")],
            builtin=False,
        )
    )
    registry.register_dynamic_command("rawsend", rawsend_handler)
    registry.register_watcher(watcher)
