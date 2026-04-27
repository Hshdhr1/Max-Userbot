"""Max Userbot - точка входа с поддержкой мультиаккаунта.

Запускает все активные аккаунты из accounts.json или выполняет первичную авторизацию.
"""

import asyncio
import importlib.util
import logging
import signal
from pathlib import Path

from core import loader as core_loader
from core.db import db as kv_db
from core.multiaccount import AccountEntry as MultiAccountEntry
from core.multiaccount import multiaccount_manager
from userbot import (
    SESSION_FILE,
    account_store,
    config_store,
    module_registry,
    on_packet,
    webui,
    weather_client,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
logger = logging.getLogger("max-userbot.main")

CORE_MODULES_DIR = Path(__file__).resolve().parent / "core_modules"


def _load_core_modules() -> None:
    """Импортирует и регистрирует встроенные модули из ./core_modules/.

    Поддерживается два API:
    - старый: `setup(registry)`-функция в файле модуля.
    - новый: класс(ы), наследующие `core.loader.Module` (Hikka-style) — будут
      инстанциированы и зарегистрированы через `core_loader.discover_and_register`
      (но без активного клиента — он подцепится при первом подходящем connect).
    """
    if not CORE_MODULES_DIR.exists():
        return

    for path in sorted(CORE_MODULES_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"core_modules.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            setup = getattr(mod, "setup", None)
            if callable(setup):
                setup(module_registry)
                logger.info("Загружен core-модуль (legacy): %s", path.stem)
        except Exception as exc:  # noqa: BLE001
            logger.error("Не удалось загрузить core-модуль %s: %s", path.name, exc)


async def _load_class_modules(client) -> None:
    """Сканирует ./modules и ./core_modules на класс-модули нового API."""
    seen_files: set[Path] = set()

    candidates: list[Path] = []
    for directory in (CORE_MODULES_DIR, Path("modules")):
        if directory.exists():
            for path in sorted(directory.rglob("*.py")):
                if path.name.startswith("_"):
                    continue
                candidates.append(path)

    for path in candidates:
        if path in seen_files:
            continue
        seen_files.add(path)
        module_name = f"_classmod_{path.stem}_{abs(hash(str(path))) % 100000}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            instances = await core_loader.discover_and_register(
                mod, module_registry, client, kv_db
            )
            if instances:
                logger.info(
                    "Загружено class-модулей из %s: %s",
                    path.name,
                    ", ".join(i.strings.get("name", type(i).__name__) for i in instances),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось загрузить class-модуль %s: %s", path.name, exc)


def _migrate_legacy_session() -> None:
    """Если есть max_session.txt от одиночного запуска — добавляем как аккаунт `main`."""
    if not SESSION_FILE.exists():
        return
    if "main" in multiaccount_manager.accounts:
        return
    try:
        raw = SESSION_FILE.read_text(encoding="utf-8").strip()
        if "\n" not in raw:
            return
        device_id, token = raw.split("\n", maxsplit=1)
    except OSError as exc:
        logger.warning("Не удалось прочитать legacy session: %s", exc)
        return

    # Подбираем телефон из старого accounts.json (формат userbot.AccountStore).
    phone = ""
    for legacy in account_store.load():
        if legacy.label.lower() == "main":
            phone = legacy.phone
            break

    multiaccount_manager.accounts["main"] = MultiAccountEntry(
        label="main",
        phone=phone,
        state="authorized" if token else "pending_auth",
        device_id=device_id,
        token=token,
    )
    multiaccount_manager._save_accounts()

    # Также сохраняем в sessions/main.session — connect_account ожидает этот файл.
    session_dir = Path("sessions")
    session_dir.mkdir(exist_ok=True)
    session_file = session_dir / "main.session"
    if not session_file.exists():
        import json

        session_file.write_text(
            json.dumps({"token": token, "device_id": device_id}),
            encoding="utf-8",
        )
    logger.info("Legacy max_session.txt мигрирован в multi-account как 'main'")


async def main():
    """Основная функция запуска."""
    # Создаём директории
    Path("modules").mkdir(exist_ok=True)
    Path("sessions").mkdir(exist_ok=True)

    # Загружаем конфиги
    config_store.load()

    # Регистрируем встроенные модули из ./core_modules (legacy setup(registry) API)
    _load_core_modules()

    # Регистрируем class-модули (Hikka-style) из ./core_modules и ./modules
    # client здесь None — он становится доступен после connect_account, но команды
    # уже зарегистрированы и будут диспатчиться корректно.
    await _load_class_modules(client=None)

    # Регистрируем callback по умолчанию, чтобы все аккаунты получали обработчик автоматически
    multiaccount_manager.set_default_callback(on_packet)

    # Миграция со старого max_session.txt
    _migrate_legacy_session()

    # Запускаем Web UI
    web_url = await webui.start()
    logger.info(f"Web UI запущен: {web_url}")

    # Подключаем все аккаунты
    await multiaccount_manager.connect_all()

    active_accounts = multiaccount_manager.get_all_accounts()

    if not active_accounts:
        logger.warning("Нет активных аккаунтов. Добавьте через Web UI или команды.")
        logger.info("Добавьте аккаунт командой: .addaccount <label> <phone>")
        logger.info("Затем подключите: .connectacc <label>")
        logger.info("И отправьте SMS код: .sendcode <label>")
    else:
        logger.info(f"Подключено аккаунтов: {len(active_accounts)}")
        for acc in active_accounts:
            status = "✅ авторизован" if acc.authorized else "⏳ ожидает входа"
            logger.info(f"  • {acc.label} ({acc.phone}) - {status}")

    # Подготавливаем graceful shutdown по SIGINT/SIGTERM.
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("Получен сигнал остановки, завершаемся...")
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except (NotImplementedError, RuntimeError):
            # Windows / окружения без поддержки add_signal_handler — оставляем дефолт.
            pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("Остановка по KeyboardInterrupt...")
    finally:
        await core_loader.on_unload_all()
        await multiaccount_manager.disconnect_all()
        await webui.stop()
        await weather_client.close()
        logger.info("Userbot остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Подавляем шум при Ctrl+C — graceful shutdown уже выполнен внутри main().
        pass
