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
from core import loader, utils

@loader.tds
class MultiAccountModule(loader.Module):
    """Управление несколькими аккаунтами"""

    strings = {
        "name": "MultiAccount",
        "acc_added": "Аккаунт <b>{}</b> добавлен\nТеперь подключите его: <code>.connectacc {}</code>",
        "acc_exists": "Ошибка: Аккаунт с такой меткой уже существует",
        "acc_connected": "Аккаунт <b>{}</b> подключен и авторизован ✅",
        "acc_pending": "Аккаунт <b>{}</b> подключен\nТребуется авторизация: <code>.sendcode {}</code>",
        "acc_not_found": "Не удалось найти/подключить аккаунт <b>{}</b>",
        "acc_disconnected": "Аккаунт <b>{}</b> отключен",
        "acc_not_active": "Аккаунт <b>{}</b> не найден среди активных",
        "sms_sent": "SMS код отправлен на номер аккаунта <b>{}</b>\nВведите код: <code>.loginacc {} &lt;code&gt;</code>",
        "sms_fail": "Не удалось отправить SMS для аккаунта <b>{}</b>",
        "login_success": "Аккаунт <b>{}</b> успешно авторизован ✅",
        "login_fail": "Не удалось войти в аккаунт <b>{}</b>. Проверьте код.",
        "acc_removed": "Аккаунт <b>{}</b> удален",
        "list_header": "<b>Все аккаунты:</b>",
        "list_item": "• {}: {}\n  Статус: {}, {}",
        "no_accs": "Нет добавленных аккаунтов"
    }

    @loader.command(ru_doc="<label> <phone> - Добавить аккаунт")
    async def addaccount(self, message):
        from core.multiaccount import multiaccount_manager
        args = utils.get_args(message)
        if len(args) != 2:
            await utils.answer(message, "Использование: .addaccount <label> <phone>")
            return

        label, phone = args
        try:
            multiaccount_manager.add_account(label, phone)
            await utils.answer(message, self.strings["acc_added"].format(html.escape(label), html.escape(label)))
        except ValueError:
            await utils.answer(message, self.strings["acc_exists"])

    @loader.command(ru_doc="<label> - Подключить аккаунт")
    async def connectacc(self, message):
        from core.multiaccount import multiaccount_manager
        label = utils.get_args_raw(message)
        if not label:
            await utils.answer(message, "Использование: .connectacc <label>")
            return

        active = await multiaccount_manager.connect_account(label)
        if not active:
            await utils.answer(message, self.strings["acc_not_found"].format(html.escape(label)))
            return

        if active.authorized:
            await utils.answer(message, self.strings["acc_connected"].format(html.escape(label)))
        else:
            await utils.answer(message, self.strings["acc_pending"].format(html.escape(label), html.escape(label)))

    @loader.command(ru_doc="<label> - Отключить аккаунт")
    async def disconnectacc(self, message):
        from core.multiaccount import multiaccount_manager
        label = utils.get_args_raw(message)
        if not label:
            await utils.answer(message, "Использование: .disconnectacc <label>")
            return

        success = await multiaccount_manager.disconnect_account(label)
        if success:
            await utils.answer(message, self.strings["acc_disconnected"].format(html.escape(label)))
        else:
            await utils.answer(message, self.strings["acc_not_active"].format(html.escape(label)))

    @loader.command(ru_doc="- Список всех аккаунтов")
    async def listacc(self, message):
        from core.multiaccount import multiaccount_manager
        all_accounts = list(multiaccount_manager.accounts.values())
        active_accounts = multiaccount_manager.get_all_accounts()
        active_labels = {acc.label for acc in active_accounts}

        if not all_accounts:
            await utils.answer(message, self.strings["no_accs"])
            return

        lines = [self.strings["list_header"]]
        for acc in all_accounts:
            status = "🟢 активен" if acc.label in active_labels else "🔴 отключен"
            auth_status = "✅ авторизован" if acc.state == "authorized" else "⏳ ожидает входа"
            lines.append(self.strings["list_item"].format(
                html.escape(acc.label), html.escape(acc.phone), status, auth_status
            ))

        await utils.answer(message, "\n".join(lines))

    @loader.command(ru_doc="<label> - Отправить SMS код")
    async def sendcode(self, message):
        from core.multiaccount import multiaccount_manager
        label = utils.get_args_raw(message)
        if not label:
            await utils.answer(message, "Использование: .sendcode <label>")
            return

        sms_token = await multiaccount_manager.send_code(label)
        if sms_token:
            await utils.answer(message, self.strings["sms_sent"].format(html.escape(label), html.escape(label)))
        else:
            await utils.answer(message, self.strings["sms_fail"].format(html.escape(label)))

    @loader.command(ru_doc="<label> <code> - Войти по SMS коду")
    async def loginacc(self, message):
        from core.multiaccount import multiaccount_manager
        args = utils.get_args(message)
        if len(args) != 2:
            await utils.answer(message, "Использование: .loginacc <label> <code>")
            return

        label, code_str = args
        try:
            sms_code = int(code_str)
        except ValueError:
            await utils.answer(message, "Код должен быть числом")
            return

        success = await multiaccount_manager.login_by_sms(label, sms_code)
        if success:
            await utils.answer(message, self.strings["login_success"].format(html.escape(label)))
        else:
            await utils.answer(message, self.strings["login_fail"].format(html.escape(label)))

    @loader.command(ru_doc="<label> - Удалить аккаунт")
    async def removeacc(self, message):
        from core.multiaccount import multiaccount_manager
        label = utils.get_args_raw(message)
        if not label:
            await utils.answer(message, "Использование: .removeacc <label>")
            return

        success = multiaccount_manager.remove_account(label)
        if success:
            await utils.answer(message, self.strings["acc_removed"].format(html.escape(label)))
        else:
            await utils.answer(message, self.strings["acc_not_active"].format(html.escape(label)))
