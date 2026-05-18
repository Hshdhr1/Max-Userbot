from core import loader, utils
import psutil
import time
import os

class SystemInfoModule(loader.Module):
    """Информация о системе"""
    strings = {"name": "SystemInfo"}

    @loader.command(ru_doc="Показать информацию о системе")
    async def sysinfo(self, message):
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        uptime = time.time() - psutil.boot_time()

        res = (
            "<b>💻 System Info:</b>\n"
            f"CPU: <code>{cpu}%</code>\n"
            f"RAM: <code>{mem}%</code>\n"
            f"Uptime: <code>{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m</code>"
        )
        await utils.answer(message, res)
