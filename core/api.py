"""API-обёртка над runtime объектами Max Userbot.

Слой для будущего переноса логики из userbot.py в пакетную структуру `core/`.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class CoreAPI:
    """Унифицированный доступ к расширениям API."""

    api: "MaxApiExtensions"
    
    async def send_raw(self, opcode: int, payload: dict) -> dict:
        """Отправка raw opcode."""
        return await self.api.send_raw(opcode, payload)
    
    async def react(self, chat_id: int, message_id: str, emoji: str) -> dict:
        """Отправка реакции."""
        return await self.api.react(chat_id, message_id, emoji)
    
    async def update_profile(
        self, 
        first_name: str | None = None, 
        last_name: str | None = None, 
        bio: str | None = None
    ) -> dict:
        """Обновление профиля."""
        return await self.api.update_profile(first_name, last_name, bio)


class MaxApiExtensions:
    """Расширения API для MaxClient."""
    
    def __init__(self, client):
        self.client = client
    
    async def send_raw(self, opcode: int, payload: dict) -> dict:
        """Отправка произвольного opcode."""
        if hasattr(self.client, "send_packet"):
            return await self.client.send_packet(opcode=opcode, payload=payload)
        raise RuntimeError("send_packet method is unavailable in current vkmax build")
    
    async def react(self, chat_id: int, message_id: str, emoji: str) -> dict:
        """Отправка реакции на сообщение."""
        return await self.send_raw(
            178,
            {
                "chatId": chat_id,
                "messageId": str(message_id),
                "reaction": {"reactionType": "EMOJI", "id": emoji},
            },
        )
    
    async def update_profile(
        self, 
        first_name: str | None = None, 
        last_name: str | None = None, 
        bio: str | None = None
    ) -> dict:
        """Обновление профиля пользователя."""
        settings_payload: dict[str, Any] = {"user": {}}
        if first_name is not None:
            settings_payload["user"]["firstName"] = first_name
        if last_name is not None:
            settings_payload["user"]["lastName"] = last_name
        if bio is not None:
            settings_payload["user"]["bio"] = bio
        return await self.send_raw(22, {"settings": settings_payload})
    
    async def start_call(
        self, 
        chat_id: int, 
        user_id: int, 
        video: bool = False
    ) -> dict | None:
        """Начало звонка.
        
        Примечание: opcode для звонков может отличаться в зависимости от версии vkmax.
        Текущие opcode (200-203) являются предположительными и могут потребовать уточнения.
        """
        call_type = "video" if video else "audio"
        return await self.send_raw(
            200,
            {
                "chatId": chat_id,
                "userId": user_id,
                "callType": call_type
            }
        )
    
    async def accept_call(self, call_id: str) -> dict | None:
        """Принятие звонка."""
        return await self.send_raw(201, {"callId": call_id})
    
    async def end_call(self, call_id: str) -> dict | None:
        """Завершение звонка."""
        return await self.send_raw(202, {"callId": call_id})
    
    async def reject_call(self, call_id: str) -> dict | None:
        """Отклонение звонка."""
        return await self.send_raw(203, {"callId": call_id})


__all__ = ["CoreAPI", "MaxApiExtensions"]
