# meta developer: @maxub
# scope: maxub
# requires: aiohttp
__version__ = (1, 0, 0)

from core import loader


@loader.tds
class PingModule(loader.Module):
    strings = {
        "name": "PingModule",
        "pong": "🏓 pong",
    }

    @loader.command("ping", "Проверка доступности бота")
    async def ping_cmd(self, message_ctx: dict, args_raw: str) -> str:
        _ = message_ctx, args_raw
        return self.strings["pong"]
