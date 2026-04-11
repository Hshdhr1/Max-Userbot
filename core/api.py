"""API-обёртка над runtime объектами Max Userbot.

Слой для будущего переноса логики из userbot.py в пакетную структуру `core/`.
"""

from dataclasses import dataclass

from userbot import MaxApiExtensions


@dataclass
class CoreAPI:
    """Унифицированный доступ к расширениям API."""

    api: MaxApiExtensions
