# webui

Папка под фронтенд/бэкенд Web UI.

- текущая реализация: `WebUIManager` в `userbot.py`
- экспорт для импорта: `webui/app.py`

План:
1. Вынести HTML/CSS шаблоны в `webui/templates/`
2. Разделить API роуты (`/api/accounts`, `/api/config`) в отдельный модуль
3. Добавить аутентификацию для Web UI
