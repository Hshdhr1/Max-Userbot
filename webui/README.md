# webui

Папка под фронтенд/бэкенд Web UI.

Текущие возможности:
- каталог модулей через `GET /api/modules/catalog`
- SSE-стрим логов через `GET /api/logs/stream`
- управление аккаунтами через `POST /api/accounts`
- управление конфигами модулей через `POST /api/config`

Текущая реализация живет в `userbot.py` (`WebUIManager`),
а `webui/app.py` — экспорт-точка для дальнейшего рефакторинга.
