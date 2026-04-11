# meta developer: @example
# scope: maxub
# requires: vkmax
__version__ = (1, 0, 0)

from core import loader


@loader.tds
class EchoModule(loader.Module):
    strings = {
        "name": "EchoModule",
    }

    @loader.command("echo", "Повторить текст")
    async def echo_cmd(self, message_ctx: dict, args_raw: str) -> str:
        _ = message_ctx
        if not args_raw:
            return "Использование: .echo <текст>"
        return args_raw
