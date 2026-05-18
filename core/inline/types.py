from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class InlineCall:
    """Объект callback-запроса."""

    data: str
    chat_id: int
    message_id: int
    sender_id: int
    _client: Any

    async def answer(self, text: str = "", show_alert: bool = False) -> None:
        """Ответить на callback (no-op для Max)."""
        pass

    async def edit(self, text: str, reply_markup: Optional[list] = None) -> None:
        """Отредактировать сообщение с кнопками."""
        from userbot import edit_message
        await edit_message(self._client, self.chat_id, self.message_id, text)

    async def delete(self) -> None:
        """Удалить сообщение."""
        # У MaxClient нет удаления в текущем адаптере, но мы можем отредактировать в пустоту
        from userbot import edit_message
        await edit_message(self._client, self.chat_id, self.message_id, "🗑")
