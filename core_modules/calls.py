"""Модуль звонков для Max Userbot.

Предоставляет команды для управления звонками через API vkmax.
"""

import html
import re

from userbot import BotModule, ModuleCommand


def setup(registry):
    """Регистрация модуля звонков."""
    
    registry.register_module(
        BotModule(
            name="Calls",
            description="Управление звонками (audio/video)",
            commands=[
                ModuleCommand(
                    name="call",
                    description="Начать звонок .call <user_id> [audio|video]",
                    aliases=["звонок"]
                ),
                ModuleCommand(
                    name="acceptcall",
                    description="Принять звонок .acceptcall <call_id>",
                    aliases=["принятьзвонок"]
                ),
                ModuleCommand(
                    name="endcall",
                    description="Завершить звонок .endcall <call_id>",
                    aliases=["завершитьзвонок"]
                ),
                ModuleCommand(
                    name="rejectcall",
                    description="Отклонить звонок .rejectcall <call_id>",
                    aliases=["отклонитьзвонок"]
                ),
                ModuleCommand(
                    name="activcalls",
                    description="Показать активные звонки",
                    aliases=["активзвонки"]
                ),
            ],
            builtin=True,
            version="1.0.0"
        )
    )
    
    # Регистрация динамических команд будет выполнена в process_builtin
    registry.register_dynamic_command("call", handle_call)
    registry.register_dynamic_command("acceptcall", handle_accept_call)
    registry.register_dynamic_command("endcall", handle_end_call)
    registry.register_dynamic_command("rejectcall", handle_reject_call)
    registry.register_dynamic_command("activcalls", handle_active_calls)


async def handle_call(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Обработчик команды начала звонка."""
    from core.client_manager import call_manager
    
    if not arg:
        return "Использование: .call <user_id> [audio|video]"
    
    parts = arg.split()
    if len(parts) < 1:
        return "Использование: .call <user_id> [audio|video]"
    
    try:
        user_id = int(parts[0])
    except ValueError:
        return "user_id должен быть числом"
    
    call_type = "video" if len(parts) > 1 and parts[1].lower() == "video" else "audio"
    
    # Используем первый доступный аккаунт
    clients = call_manager.get_all_clients()
    if not clients:
        return "Нет активных клиентов"
    
    client_label = clients[0].label
    
    result = await call_manager.start_call(
        client_label=client_label,
        chat_id=chat_id,
        user_id=user_id,
        video=(call_type == "video")
    )
    
    if result:
        call_id = result.get("callId", "unknown")
        return f"Звонок начат\nCall ID: <code>{html.escape(call_id)}</code>\nТип: {call_type}"
    else:
        return "Не удалось начать звонок"


async def handle_accept_call(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Обработчик команды принятия звонка."""
    from core.client_manager import call_manager
    
    if not arg:
        return "Использование: .acceptcall <call_id>"
    
    call_id = arg.strip()
    clients = call_manager.get_all_clients()
    
    if not clients:
        return "Нет активных клиентов"
    
    result = await call_manager.accept_call(clients[0].label, call_id)
    
    if result:
        return f"Звонок {html.escape(call_id)} принят"
    else:
        return "Не удалось принять звонок"


async def handle_end_call(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Обработчик команды завершения звонка."""
    from core.client_manager import call_manager
    
    if not arg:
        return "Использование: .endcall <call_id>"
    
    call_id = arg.strip()
    clients = call_manager.get_all_clients()
    
    if not clients:
        return "Нет активных клиентов"
    
    result = await call_manager.end_call(clients[0].label, call_id)
    
    if result:
        return f"Звонок {html.escape(call_id)} завершён"
    else:
        return "Не удалось завершить звонок"


async def handle_reject_call(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Обработчик команды отклонения звонка."""
    from core.client_manager import call_manager
    
    if not arg:
        return "Использование: .rejectcall <call_id>"
    
    call_id = arg.strip()
    clients = call_manager.get_all_clients()
    
    if not clients:
        return "Нет активных клиентов"
    
    result = await call_manager.reject_call(clients[0].label, call_id)
    
    if result:
        return f"Звонок {html.escape(call_id)} отклонён"
    else:
        return "Не удалось отклонить звонок"


async def handle_active_calls(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Обработчик команды показа активных звонков."""
    from core.client_manager import call_manager
    
    calls = call_manager.get_active_calls()
    
    if not calls:
        return "Нет активных звонков"
    
    lines = ["<b>Активные звонки:</b>"]
    for call in calls:
        lines.append(
            f"📞 <code>{html.escape(call.call_id)}</code>\n"
            f"   Чат: {call.chat_id}\n"
            f"   Статус: {call.status}\n"
            f"   Тип: {call.type}"
        )
    
    return "\n".join(lines)
