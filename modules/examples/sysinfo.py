"""Модуль для получения информации о системе и текущем времени."""

import datetime
import platform
import psutil
from core import loader, utils

@loader.tds
class SystemInfoModule(loader.Module):
    """Информация о системе"""

    strings = {
        "name": "SystemInfo",
        "info": (
            "💻 <b>Системная информация:</b>\n"
            "• <b>ОС:</b> {} {}\n"
            "• <b>Архитектура:</b> {}\n"
            "• <b>Python:</b> {}\n"
            "• <b>ЦП:</b> {}% ({} ядер)\n"
            "• <b>ОЗУ:</b> {}% ({}/{} MB)\n"
            "• <b>Время:</b> {}"
        )
    }

    @loader.command(ru_doc="- Показать информацию о системе")
    async def sysinfo(self, message):
        mem = psutil.virtual_memory()
        cpu_usage = psutil.cpu_percent()
        cpu_count = psutil.cpu_count()

        info_text = self.strings["info"].format(
            platform.system(), platform.release(),
            platform.machine(),
            platform.python_version(),
            cpu_usage, cpu_count,
            mem.percent, int(mem.used / 1024 / 1024), int(mem.total / 1024 / 1024),
            datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        )
        await utils.answer(message, info_text)

    @loader.command(ru_doc="- Пинг")
    async def ping(self, message):
        """Проверка задержки"""
        import time
        start = time.perf_counter()
        await message.edit("🏓 <b>Pong!</b>")
        end = time.perf_counter()
        ms = (end - start) * 1000
        await message.edit(f"🏓 <b>Pong!</b>\nЗадержка: <code>{ms:.2f} ms</code>")
