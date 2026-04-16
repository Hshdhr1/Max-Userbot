"""Модуль управления мультиаккаунтами.

Команды:
- .addaccount <label> <phone> - Добавить аккаунт
- .connectacc <label> - Подключить аккаунт
- .disconnectacc <label> - Отключить аккаунт
- .listacc - Список всех аккаунтов
- .sendcode <label> - Отправить SMS код
- .loginacc <label> <code> - Войти по SMS коду
"""

import html

from userbot import BotModule, ModuleCommand


def setup(registry):
    """Регистрация модуля мультиаккаунтов."""
    
    registry.register_module(
        BotModule(
            name="MultiAccount",
            description="Управление несколькими аккаунтами",
            commands=[
                ModuleCommand(
                    name="addaccount",
                    description="Добавить аккаунт .addaccount <label> <phone>",
                    aliases=["добавитьакк"]
                ),
                ModuleCommand(
                    name="connectacc",
                    description="Подключить аккаунт .connectacc <label>",
                    aliases=["подключитьакк"]
                ),
                ModuleCommand(
                    name="disconnectacc",
                    description="Отключить аккаунт .disconnectacc <label>",
                    aliases=["отключитьакк"]
                ),
                ModuleCommand(
                    name="listacc",
                    description="Список всех аккаунтов",
                    aliases=["списокакк"]
                ),
                ModuleCommand(
                    name="sendcode",
                    description="Отправить SMS код .sendcode <label>",
                    aliases=["отправитькод"]
                ),
                ModuleCommand(
                    name="loginacc",
                    description="Войти по SMS .loginacc <label> <code>",
                    aliases=["войтиакк"]
                ),
                ModuleCommand(
                    name="removeacc",
                    description="Удалить аккаунт .removeacc <label>",
                    aliases=["удалитьакк"]
                ),
            ],
            builtin=True,
            version="1.0.0"
        )
    )
    
    # Регистрация динамических команд
    registry.register_dynamic_command("addaccount", handle_add_account)
    registry.register_dynamic_command("connectacc", handle_connect_account)
    registry.register_dynamic_command("disconnectacc", handle_disconnect_account)
    registry.register_dynamic_command("listacc", handle_list_accounts)
    registry.register_dynamic_command("sendcode", handle_send_code)
    registry.register_dynamic_command("loginacc", handle_login_account)
    registry.register_dynamic_command("removeacc", handle_remove_account)


async def handle_add_account(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Добавление аккаунта."""
    from core.multiaccount import multiaccount_manager
    
    parts = arg.split(maxsplit=1)
    if len(parts) != 2:
        return "Использование: .addaccount <label> <phone>"
    
    label, phone = parts
    
    try:
        multiaccount_manager.add_account(label, phone)
        return f"Аккаунт {html.escape(label)} добавлен\nТеперь подключите его: .connectacc {html.escape(label)}"
    except ValueError as e:
        return f"Ошибка: {html.escape(str(e))}"


async def handle_connect_account(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Подключение аккаунта."""
    from core.multiaccount import multiaccount_manager
    
    label = arg.strip()
    if not label:
        return "Использование: .connectacc <label>"
    
    active = await multiaccount_manager.connect_account(label)
    
    if not active:
        return f"Не удалось подключить аккаунт {html.escape(label)}"
    
    if active.authorized:
        return f"Аккаунт {html.escape(label)} подключен и авторизован ✅"
    else:
        return f"Аккаунт {html.escape(label)} подключен\nТребуется авторизация: .sendcode {html.escape(label)}"


async def handle_disconnect_account(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Отключение аккаунта."""
    from core.multiaccount import multiaccount_manager
    
    label = arg.strip()
    if not label:
        return "Использование: .disconnectacc <label>"
    
    success = await multiaccount_manager.disconnect_account(label)
    
    if success:
        return f"Аккаунт {html.escape(label)} отключен"
    else:
        return f"Аккаунт {html.escape(label)} не найден среди активных"


async def handle_list_accounts(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Список аккаунтов."""
    from core.multiaccount import multiaccount_manager
    
    all_accounts = list(multiaccount_manager.accounts.values())
    active_accounts = multiaccount_manager.get_all_accounts()
    active_labels = {acc.label for acc in active_accounts}
    
    if not all_accounts:
        return "Нет добавленных аккаунтов"
    
    lines = ["<b>Все аккаунты:</b>"]
    for acc in all_accounts:
        status = "🟢 активен" if acc.label in active_labels else "🔴 отключен"
        auth_status = "✅ авторизован" if acc.state == "authorized" else "⏳ ожидает входа"
        lines.append(f"• {html.escape(acc.label)}: {html.escape(acc.phone)}")
        lines.append(f"  Статус: {status}, {auth_status}")
    
    return "\n".join(lines)


async def handle_send_code(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Отправка SMS кода."""
    from core.multiaccount import multiaccount_manager
    
    label = arg.strip()
    if not label:
        return "Использование: .sendcode <label>"
    
    sms_token = await multiaccount_manager.send_code(label)
    
    if sms_token:
        return f"SMS код отправлен на номер аккаунта {html.escape(label)}\nВведите код: .loginacc {html.escape(label)} <code>"
    else:
        return f"Не удалось отправить SMS для аккаунта {html.escape(label)}"


async def handle_login_account(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Вход по SMS коду."""
    from core.multiaccount import multiaccount_manager
    
    parts = arg.split(maxsplit=1)
    if len(parts) != 2:
        return "Использование: .loginacc <label> <code>"
    
    label, code_str = parts
    
    try:
        sms_code = int(code_str)
    except ValueError:
        return "Код должен быть числом"
    
    success = await multiaccount_manager.login_by_sms(label, sms_code)
    
    if success:
        return f"Аккаунт {html.escape(label)} успешно авторизован ✅"
    else:
        return f"Не удалось войти в аккаунт {html.escape(label)}. Проверьте код."


async def handle_remove_account(ctx, chat_id: int, message_id: int, arg: str) -> str:
    """Удаление аккаунта."""
    from core.multiaccount import multiaccount_manager
    
    label = arg.strip()
    if not label:
        return "Использование: .removeacc <label>"
    
    success = multiaccount_manager.remove_account(label)
    
    if success:
        return f"Аккаунт {html.escape(label)} удален"
    else:
        return f"Аккаунт {html.escape(label)} не найден"
