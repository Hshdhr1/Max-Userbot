"""Конфигурационный слой проекта.

Сейчас является thin-wrapper вокруг ConfigStore из userbot.py.
"""

from userbot import ConfigStore, UserbotConfig

__all__ = ["ConfigStore", "UserbotConfig"]
