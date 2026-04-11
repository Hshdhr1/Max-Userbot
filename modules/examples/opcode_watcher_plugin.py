# meta developer: @example
# scope: maxub
# requires: vkmax
__version__ = (1, 0, 0)

from core import loader


@loader.tds
class OpcodeToolsModule(loader.Module):
    strings = {
        "name": "OpcodeTools",
    }

    @loader.command("rawhelp", "Подсказка по отправке opcode")
    async def rawhelp_cmd(self, message_ctx: dict, args_raw: str) -> str:
        _ = message_ctx, args_raw
        return "Используй встроенные runtime-инструменты для отправки opcode из userbot API"
