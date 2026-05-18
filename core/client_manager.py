"""Менеджер звонков для Max Userbot.

Работает в связке с мультиаккаунт менеджером.
Важно: opcode для звонков (200-203) являются предположительными.
Необходимо уточнить актуальные opcode в документации vkmax или через сниффинг трафика.
"""

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger("max-userbot.calls")


@dataclass
class CallInfo:
    """Информация о звонке."""
    
    call_id: str
    chat_id: int
    caller_id: int
    callee_id: int
    status: str  # ringing, connected, ended
    type: str = "audio"  # audio, video
    duration: int = 0
    account_label: str = ""


class CallManager:
    """Менеджер звонков с поддержкой мультиаккаунта."""
    
    # Предположительные opcode для звонков
    OP_INCOMING_CALL = 199 # Уведомление о входящем
    OP_START_CALL = 200    # Начать/Исходящий
    OP_ACCEPT_CALL = 201   # Принять
    OP_END_CALL = 202      # Завершить
    OP_REJECT_CALL = 203   # Отклонить
    
    def __init__(self):
        self.active_calls: dict[str, CallInfo] = {}
        self.call_handlers: list[Callable] = []
        self._multiaccount_manager = None

        # Настройки
        self.auto_accept = False
        self.auto_reject = False
        self.blacklist_users = set()
    
    def set_multiaccount_manager(self, manager):
        """Установка ссылки на мультиаккаунт менеджер."""
        self._multiaccount_manager = manager
    
    def _get_client_by_label(self, label: str):
        """Получение клиента по метке."""
        if not self._multiaccount_manager:
            logger.error("MultiAccountManager не установлен")
            return None
        return self._multiaccount_manager.get_account(label)
    
    def get_all_clients(self):
        """Получение всех активных клиентов."""
        if not self._multiaccount_manager:
            return []
        return self._multiaccount_manager.get_all_accounts()
    
    async def start_call(
        self,
        client_label: str,
        chat_id: int, 
        user_id: int,
        video: bool = False
    ) -> dict | None:
        """Начало звонка от имени конкретного аккаунта."""
        active = self._get_client_by_label(client_label)
        if not active:
            logger.error(f"Аккаунт {client_label} не найден")
            return None
        
        call_type = "video" if video else "audio"
        
        result = await active.api.send_raw(
            opcode=self.OP_START_CALL,
            payload={
                "chatId": chat_id,
                "userId": user_id,
                "callType": call_type
            }
        )
        
        if result:
            call_id = result.get("callId", f"{chat_id}_{user_id}")
            self.active_calls[call_id] = CallInfo(
                call_id=call_id,
                chat_id=chat_id,
                caller_id=0,  # Будет заполнено позже
                callee_id=user_id,
                status="ringing",
                type=call_type,
                account_label=client_label
            )
            logger.info(f"Звонок начат от {client_label}: {call_id}")
        
        return result
    
    async def accept_call(self, client_label: str, call_id: str) -> dict | None:
        """Принятие звонка."""
        active = self._get_client_by_label(client_label)
        if not active:
            return None
        
        result = await active.api.send_raw(
            opcode=self.OP_ACCEPT_CALL,
            payload={"callId": call_id}
        )
        
        if call_id in self.active_calls:
            self.active_calls[call_id].status = "connected"
        
        return result
    
    async def end_call(self, client_label: str, call_id: str) -> dict | None:
        """Завершение звонка."""
        active = self._get_client_by_label(client_label)
        if not active:
            return None
        
        result = await active.api.send_raw(
            opcode=self.OP_END_CALL,
            payload={"callId": call_id}
        )
        
        if call_id in self.active_calls:
            del self.active_calls[call_id]
        
        return result
    
    async def reject_call(self, client_label: str, call_id: str) -> dict | None:
        """Отклонение звонка."""
        active = self._get_client_by_label(client_label)
        if not active:
            return None
        
        result = await active.api.send_raw(
            opcode=self.OP_REJECT_CALL,
            payload={"callId": call_id}
        )
        
        if call_id in self.active_calls:
            del self.active_calls[call_id]
        
        return result
    
    def register_handler(self, handler: Callable) -> None:
        """Регистрация обработчика событий звонков."""
        self.call_handlers.append(handler)
    
    def get_active_calls(self) -> list[CallInfo]:
        """Получение активных звонков."""
        return list(self.active_calls.values())
    
    def get_active_calls_for_account(self, account_label: str) -> list[CallInfo]:
        """Получение активных звонков для конкретного аккаунта."""
        return [c for c in self.active_calls.values() if c.account_label == account_label]

    async def handle_packet(self, client_label: str, packet: dict):
        """Обработка пакетов звонков от MaxClient."""
        opcode = packet.get("opcode")
        payload = packet.get("payload", {})

        # Входящий звонок
        if opcode == self.OP_INCOMING_CALL:
            call_id = payload.get("callId")
            caller_id = payload.get("callerId")
            chat_id = payload.get("chatId", 0)

            logger.info(f"[{client_label}] Входящий звонок {call_id} от {caller_id}")

            self.active_calls[call_id] = CallInfo(
                call_id=call_id,
                chat_id=chat_id,
                caller_id=caller_id,
                callee_id=0, # Мы
                status="ringing",
                account_label=client_label
            )

            if self.auto_reject or caller_id in self.blacklist_users:
                logger.info(f"[{client_label}] Авто-отклонение звонка {call_id}")
                await self.reject_call(client_label, call_id)
            elif self.auto_accept:
                logger.info(f"[{client_label}] Авто-принятие звонка {call_id}")
                await self.accept_call(client_label, call_id)

        # Обновление статуса (если есть пакеты изменения состояния)
        elif opcode == self.OP_END_CALL:
            call_id = payload.get("callId")
            if call_id in self.active_calls:
                logger.info(f"[{client_label}] Звонок {call_id} завершён удалённо")
                del self.active_calls[call_id]


# Глобальный экземпляр
call_manager = CallManager()


__all__ = ["CallManager", "CallInfo", "call_manager"]
