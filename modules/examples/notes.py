"""Модуль для работы с заметками."""

from core import loader, utils

@loader.tds
class NotesModule(loader.Module):
    """Управление заметками"""

    strings = {
        "name": "Notes",
        "note_saved": "✅ Заметка <b>{}</b> сохранена",
        "note_deleted": "🗑 Заметка <b>{}</b> удалена",
        "note_not_found": "❌ Заметка <b>{}</b> не найдена",
        "notes_list": "📝 <b>Ваши заметки:</b>\n{}",
        "no_notes": "📂 Список заметок пуст",
        "usage_save": "ℹ️ Использование: <code>.save [название] [текст]</code>",
        "usage_get": "ℹ️ Использование: <code>.get [название]</code>"
    }

    async def client_ready(self, client, db):
        self._db = db

    @loader.command(ru_doc="<название> <текст> - Сохранить заметку")
    async def save(self, message):
        args = utils.get_args(message)
        if len(args) < 2:
            await utils.answer(message, self.strings["usage_save"])
            return

        name, text = args[0], " ".join(args[1:])
        notes = self._db.get("Notes", "notes", {})
        notes[name] = text
        self._db.set("Notes", "notes", notes)
        await utils.answer(message, self.strings["note_saved"].format(name))

    @loader.command(ru_doc="<название> - Получить заметку")
    async def get(self, message):
        name = utils.get_args_raw(message)
        if not name:
            await utils.answer(message, self.strings["usage_get"])
            return

        notes = self._db.get("Notes", "notes", {})
        if name in notes:
            await utils.answer(message, notes[name])
        else:
            await utils.answer(message, self.strings["note_not_found"].format(name))

    @loader.command(ru_doc="- Список всех заметок")
    async def notes(self, message):
        notes = self._db.get("Notes", "notes", {})
        if not notes:
            await utils.answer(message, self.strings["no_notes"])
            return

        items = "\n".join([f"• <code>{name}</code>" for name in notes.keys()])
        await utils.answer(message, self.strings["notes_list"].format(items))

    @loader.command(ru_doc="<название> - Удалить заметку")
    async def delnote(self, message):
        name = utils.get_args_raw(message)
        if not name:
            await utils.answer(message, "ℹ️ Использование: <code>.delnote [название]</code>")
            return

        notes = self._db.get("Notes", "notes", {})
        if name in notes:
            del notes[name]
            self._db.set("Notes", "notes", notes)
            await utils.answer(message, self.strings["note_deleted"].format(name))
        else:
            await utils.answer(message, self.strings["note_not_found"].format(name))
