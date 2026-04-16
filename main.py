"""Max Userbot - точка входа с поддержкой мультиаккаунта.

Запускает все активные аккаунты из accounts.json или выполняет первичную авторизацию.
"""

import asyncio
import logging
from pathlib import Path

from core.multiaccount import multiaccount_manager
from core.loader import get_registry
from userbot import config_store, account_store, webui, weather_client

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S"
)
logger = logging.getLogger("max-userbot.main")


async def main():
    """Основная функция запуска."""
    # Создаём директории
    Path("modules").mkdir(exist_ok=True)
    Path("sessions").mkdir(exist_ok=True)
    
    # Загружаем конфиги
    config_store.load()
    
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
            
            # Устанавливаем обработчик пакетов если аккаунт авторизован
            if acc.authorized and acc.callback is None:
                from userbot import on_packet
                multiaccount_manager.set_callback(acc.label, on_packet)
    
    # Запускаем основной цикл
    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        logger.info("Остановка по сигналу...")
    finally:
        # Отключаем все аккаунты
        await multiaccount_manager.disconnect_all()
        await webui.stop()
        await weather_client.close()
        logger.info("Userbot остановлен")


if __name__ == "__main__":
    asyncio.run(main())
