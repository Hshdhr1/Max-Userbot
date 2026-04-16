"""Мультиаккаунт менеджер для Max Userbot.

Позволяет управлять несколькими аккаунтами одновременно.
"""

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from vkmax.client import MaxClient

from core.api import MaxApiExtensions

logger = logging.getLogger("max-userbot.multiaccount")

ACCOUNTS_FILE = Path("accounts.json")
SESSION_DIR = Path("sessions")


@dataclass
class AccountEntry:
    """Запись об аккаунте."""
    
    label: str
    phone: str
    state: str = "pending_auth"  # pending_auth, authorized, error
    device_id: str = ""
    token: str = ""


@dataclass
class ActiveAccount:
    """Активный аккаунт с клиентом."""
    
    label: str
    phone: str
    client: MaxClient
    api: MaxApiExtensions
    authorized: bool = False
    callback: Callable | None = None


class MultiAccountManager:
    """Менеджер множественных аккаунтов."""
    
    def __init__(self):
        self.accounts: dict[str, AccountEntry] = {}
        self.active_accounts: dict[str, ActiveAccount] = {}
        self._load_accounts()
        SESSION_DIR.mkdir(exist_ok=True)
    
    def _load_accounts(self) -> None:
        """Загрузка списка аккаунтов из файла."""
        if not ACCOUNTS_FILE.exists():
            return
        
        try:
            data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
            self.accounts = {
                entry["label"]: AccountEntry(**entry) 
                for entry in data
            }
            logger.info(f"Загружено {len(self.accounts)} аккаунтов")
        except Exception as exc:
            logger.warning(f"Не удалось загрузить аккаунты: {exc}")
    
    def _save_accounts(self) -> None:
        """Сохранение списка аккаунтов."""
        data = [asdict(acc) for acc in self.accounts.values()]
        ACCOUNTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def add_account(self, label: str, phone: str) -> AccountEntry:
        """Добавление аккаунта в список."""
        if label in self.accounts:
            raise ValueError(f"Аккаунт с меткой '{label}' уже существует")
        
        entry = AccountEntry(label=label, phone=phone)
        self.accounts[label] = entry
        self._save_accounts()
        logger.info(f"Добавлен аккаунт: {label} ({phone})")
        return entry
    
    def remove_account(self, label: str) -> bool:
        """Удаление аккаунта из списка."""
        if label not in self.accounts:
            return False
        
        # Сначала отключаем если активен
        if label in self.active_accounts:
            asyncio.create_task(self.disconnect_account(label))
        
        del self.accounts[label]
        self._save_accounts()
        logger.info(f"Удален аккаунт: {label}")
        return True
    
    async def connect_account(self, label: str) -> ActiveAccount | None:
        """Подключение аккаунта."""
        if label not in self.accounts:
            logger.error(f"Аккаунт {label} не найден")
            return None
        
        entry = self.accounts[label]
        client = MaxClient()
        api = MaxApiExtensions(client)
        
        await client.connect()
        
        active = ActiveAccount(
            label=label,
            phone=entry.phone,
            client=client,
            api=api
        )
        
        # Пробуем войти по токену если есть
        if entry.token and entry.device_id:
            session_file = SESSION_DIR / f"{label}.session"
            if session_file.exists():
                try:
                    session_data = json.loads(session_file.read_text(encoding="utf-8"))
                    await client.login_by_token(
                        session_data["token"],
                        session_data["device_id"]
                    )
                    active.authorized = True
                    entry.state = "authorized"
                    logger.info(f"[{label}] Вход по токену успешен")
                except Exception as exc:
                    logger.warning(f"[{label}] Вход по токену не удался: {exc}")
                    entry.state = "pending_auth"
        
        self.active_accounts[label] = active
        self._save_accounts()
        return active
    
    async def login_by_sms(
        self, 
        label: str, 
        sms_code: int
    ) -> bool:
        """Вход по SMS коду."""
        if label not in self.active_accounts:
            logger.error(f"Аккаунт {label} не подключен")
            return False
        
        active = self.active_accounts[label]
        entry = self.accounts[label]
        
        # Сначала нужно отправить код
        if not hasattr(active, "sms_token") or not active.sms_token:
            logger.error(f"[{label}] Сначала отправьте SMS код")
            return False
        
        try:
            account_data = await active.client.sign_in(active.sms_token, sms_code)
            token = account_data["payload"]["tokenAttrs"]["LOGIN"]["token"]
            
            # Сохраняем сессию
            session_file = SESSION_DIR / f"{label}.session"
            session_data = {
                "token": token,
                "device_id": active.client.device_id
            }
            session_file.write_text(json.dumps(session_data), encoding="utf-8")
            
            active.authorized = True
            entry.state = "authorized"
            entry.token = token
            entry.device_id = active.client.device_id
            self._save_accounts()
            
            logger.info(f"[{label}] Вход по SMS успешен")
            return True
        except Exception as exc:
            logger.error(f"[{label}] Вход по SMS не удался: {exc}")
            return False
    
    async def send_code(self, label: str) -> str | None:
        """Отправка SMS кода."""
        if label not in self.active_accounts:
            logger.error(f"Аккаунт {label} не подключен")
            return None
        
        active = self.active_accounts[label]
        entry = self.accounts[label]
        
        try:
            sms_token = await active.client.send_code(entry.phone)
            active.sms_token = sms_token
            logger.info(f"[{label}] SMS код отправлен")
            return sms_token
        except Exception as exc:
            logger.error(f"[{label}] Не удалось отправить SMS: {exc}")
            return None
    
    async def disconnect_account(self, label: str) -> bool:
        """Отключение аккаунта."""
        if label not in self.active_accounts:
            return False
        
        active = self.active_accounts[label]
        # Здесь можно добавить очистку соединения
        del self.active_accounts[label]
        logger.info(f"[{label}] Отключен")
        return True
    
    def set_callback(self, label: str, callback: Callable) -> bool:
        """Установка обработчика пакетов для аккаунта."""
        if label not in self.active_accounts:
            return False
        
        active = self.active_accounts[label]
        active.callback = callback
        asyncio.create_task(active.client.set_callback(callback))
        return True
    
    def get_account(self, label: str) -> ActiveAccount | None:
        """Получение активного аккаунта."""
        return self.active_accounts.get(label)
    
    def get_all_accounts(self) -> list[ActiveAccount]:
        """Получение всех активных аккаунтов."""
        return list(self.active_accounts.values())
    
    async def connect_all(self) -> None:
        """Подключение всех аккаунтов."""
        tasks = []
        for label in self.accounts:
            if label not in self.active_accounts:
                tasks.append(self.connect_account(label))
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for label, result in zip(self.accounts.keys(), results):
                if isinstance(result, Exception):
                    logger.error(f"Не удалось подключить {label}: {result}")
    
    async def disconnect_all(self) -> None:
        """Отключение всех аккаунтов."""
        tasks = [self.disconnect_account(label) for label in list(self.active_accounts.keys())]
        if tasks:
            await asyncio.gather(*tasks)
        logger.info("Все аккаунты отключены")


# Глобальный экземпляр
multiaccount_manager = MultiAccountManager()


__all__ = ["MultiAccountManager", "AccountEntry", "ActiveAccount", "multiaccount_manager"]
