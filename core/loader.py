"""Загрузчик модулей.

Используется как стартовая точка для выделения загрузки модулей в отдельный пакет `core/`.
"""

from pathlib import Path

from userbot import ModuleRegistry


def ensure_modules_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_registry() -> ModuleRegistry:
    registry = ModuleRegistry()
    registry.preload_default_modules()
    return registry
