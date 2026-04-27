"""Загрузчик модулей.

Используется как стартовая точка для выделения загрузки модулей в отдельный пакет `core/`.
"""

import importlib.util
from pathlib import Path

from userbot import ModuleRegistry

__all__ = ["ModuleRegistry", "ModuleManager", "get_registry"]


class ModuleManager:
    """Менеджер загрузки и управления модулями."""
    
    def __init__(self, registry: ModuleRegistry):
        self.registry = registry
    
    def ensure_modules_dir(self, path: Path) -> Path:
        """Создание директории модулей если не существует."""
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    async def load_module_from_path(self, path: Path, modules_dir: Path) -> str:
        """Загрузка модуля из файла."""
        if not path.exists() or path.suffix != ".py":
            raise ValueError("Укажи путь к существующему .py модулю")
        
        if modules_dir.resolve() not in path.parents and path.parent != modules_dir.resolve():
            raise ValueError("Модуль должен быть из ./modules")
        
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Не удалось загрузить модуль")
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        setup = getattr(module, "setup", None)
        if not callable(setup):
            raise ValueError("Модуль должен иметь setup(registry)")
        
        setup(self.registry)
        return f"Модуль {path.name} загружен"
    
    async def load_module_from_url(self, url: str, modules_dir: Path) -> str:
        """Загрузка модуля из URL."""
        import re
        import aiohttp
        
        if not url.startswith(("http://", "https://")):
            raise ValueError("Нужна ссылка http/https")
        
        self.ensure_modules_dir(modules_dir)
        
        filename = url.rstrip('/').split('/')[-1] or 'downloaded_module.py'
        if not filename.endswith('.py'):
            filename += '.py'
        
        safe_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
        destination = modules_dir / safe_name
        
        timeout = aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                content = await response.read()
        
        destination.write_bytes(content)
        return await self.load_module_from_path(destination, modules_dir)
    
    def unload_module(self, name: str) -> tuple[bool, str]:
        """Выгрузка модуля."""
        return self.registry.try_unload(name)


def get_registry() -> ModuleRegistry:
    """Создание и предзаполнение реестра модулей."""
    registry = ModuleRegistry()
    registry.preload_default_modules()
    return registry
