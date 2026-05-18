from core import loader, utils

class NotesModule(loader.Module):
    """Управление заметками"""
    strings = {"name": "Notes"}

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue("notes", {}, "Хранилище заметок")
        )

    @loader.command(ru_doc="<name> <text> - Сохранить заметку")
    async def save(self, message):
        args = utils.get_args(message)
        if len(args) < 2:
            await utils.answer(message, "Использование: .save <name> <text>")
            return

        name = args[0]
        text = " ".join(args[1:])

        notes = self.config["notes"]
        notes[name] = text
        self.config["notes"] = notes

        await utils.answer(message, f"Заметка <b>{name}</b> сохранена")

    @loader.command(ru_doc="<name> - Получить заметку")
    async def getnote(self, message):
        name = utils.get_args_raw(message)
        notes = self.config["notes"]

        if name in notes:
            await utils.answer(message, f"<b>{name}:</b>\n{notes[name]}")
        else:
            await utils.answer(message, "Заметка не найдена")

    @loader.command(ru_doc="- Список всех заметок")
    async def notes(self, message):
        notes = self.config["notes"]
        if not notes:
            await utils.answer(message, "Список заметок пуст")
            return

        res = "<b>Заметки:</b>\n• " + "\n• ".join(notes.keys())
        await utils.answer(message, res)

    @loader.command(ru_doc="<name> - Удалить заметку")
    async def delnote(self, message):
        name = utils.get_args_raw(message)
        notes = self.config["notes"]

        if name in notes:
            del notes[name]
            self.config["notes"] = notes
            await utils.answer(message, f"Заметка <b>{name}</b> удалена")
        else:
            await utils.answer(message, "Заметка не найдена")
