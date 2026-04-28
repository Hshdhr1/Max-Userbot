# Изменения в Max Userbot

## 🛠️ Исправления багов и улучшения (текущий PR)

### Исправлено
- **Web UI больше не падает с 500.** `WebUIManager._module_panel` обращался к
  отсутствующему атрибуту `BotModule.default_config` — добавлено поле и
  defensive `getattr` для обратной совместимости.
- **Команды `.addaccount`/`.call` теперь действительно работают.** Файлы из
  `core_modules/` ранее не загружались — добавлен авто-импорт в `main.py`.
- **`MultiAccountManager.connect_all`**: исправлен `zip` mismatch (раньше
  результаты сопоставлялись с `self.accounts.keys()`, а не с тем подмножеством
  меток, которое реально подключалось). Теперь также перечитывает `accounts.json`
  на случай изменений через Web UI.
- **`disconnect_account`** теперь действительно закрывает клиентское соединение
  (`disconnect`/`close`/`stop`), а не просто удаляет запись.
- **`set_callback`** не падает, если вызван вне работающего event loop.
- **`.setprefix`** перестал падать на пустом/пробельном аргументе.
- **`.gitignore`** очищен от тройных бэктиков на первой/последней строке (раньше
  считались литеральными паттернами).
- **`ConfigStore.load` / `AccountStore.load`** теперь устойчивы к битому JSON и
  неизвестным/пропущенным полям.
- **Удалены неиспользуемые импорты** в `core/loader.py`, `core/client_manager.py`,
  `core_modules/calls.py`; устранён ошибочный f-string без подстановок.

### Добавлено
- **`.ping`** — встроенная команда: измеряет latency event loop и uptime.
- **`/health`** — JSON health-check эндпоинт в Web UI (`status`, `uptime_seconds`,
  `modules`, `accounts`).
- **Автопривязка callback'а пакетов**: `MultiAccountManager.set_default_callback`
  + автоматическая подписка после `connect_account` и `login_by_sms`.
- **Graceful shutdown** в `main.py` через SIGINT/SIGTERM (`asyncio.Event`).
- **Миграция** legacy-сессии из `max_session.txt` в multi-account `main` при
  старте.
- **Web UI add_account** теперь синхронизируется с `MultiAccountManager`, чтобы
  добавленный через форму аккаунт был виден `connect_all`/командам.
- **Smoke-тесты** в `tests/test_smoke.py` (8 тестов: ConfigStore, BotModule,
  WebUIPanel, MultiAccountManager).
- **CI workflow** `.github/workflows/ci.yml` — ruff + unittest на Python 3.10–3.12.

---

## ✅ Выполнено

### 1. Мультиаккаунт система
- **`core/multiaccount.py`** - Менеджер множественных аккаунтов:
  - Добавление/удаление аккаунтов через `accounts.json`
  - Подключение нескольких аккаунтов одновременно
  - Авторизация по SMS с сохранением сессий в `sessions/`
  - Автоподключение при старте
  
- **`core_modules/multiaccount.py`** - Модуль команд:
  - `.addaccount <label> <phone>` - Добавить аккаунт
  - `.connectacc <label>` - Подключить аккаунт
  - `.disconnectacc <label>` - Отключить аккаунт
  - `.listacc` - Список всех аккаунтов
  - `.sendcode <label>` - Отправить SMS код
  - `.loginacc <label> <code>` - Войти по SMS коду
  - `.removeacc <label>` - Удалить аккаунт

### 2. Взаимодействие со звонками
- **`core/client_manager.py`** - CallManager:
  - Начало звонка (opcode 200)
  - Принятие звонка (opcode 201)
  - Завершение звонка (opcode 202)
  - Отклонение звонка (opcode 203)
  - Поддержка мультиаккаунта
  - Трекинг активных звонков

- **`core_modules/calls.py`** - Модуль команд:
  - `.call <user_id> [audio|video]` - Начать звонок
  - `.acceptcall <call_id>` - Принять звонок
  - `.endcall <call_id>` - Завершить звонок
  - `.rejectcall <call_id>` - Отклонить звонок
  - `.activcalls` - Показать активные звонки

### 3. Обновлённый main.py
- Интеграция с мультиаккаунт менеджером
- Автозапуск всех аккаунтов из `accounts.json`
- Web UI запускается до подключения аккаунтов
- Обработка KeyboardInterrupt для graceful shutdown

### 4. Web UI улучшения
- Форма добавления аккаунтов на главной странице
- Отображение статуса каждого аккаунта
- Конфигурация модулей через веб-интерфейс

## ⚠️ Важно

### Opcode для звонков
Opcode 200-203 являются **предположительными**. Для корректной работы звонков необходимо:
1. Протестировать с реальным сервером Max
2. При необходимости обновить opcode в `core/client_manager.py`
3. Или получить актуальные opcode из документации vkmax

### Структура проекта
```
/workspace/
├── main.py                 # Точка входа (обновлено)
├── userbot.py              # Runtime (без изменений)
├── core/                   # Ядро
│   ├── api.py              # API расширения
│   ├── client_manager.py   # Менеджер звонков (обновлено)
│   ├── config.py           # Конфигурация
│   ├── loader.py           # Загрузчик модулей
│   └── multiaccount.py     # Мультиаккаунт (обновлено)
├── core_modules/           # Встроенные модули
│   ├── calls.py            # Звонки (обновлено)
│   └── multiaccount.py     # Команды аккаунтов (обновлено)
├── modules/                # Внешние модули
├── webui/                  # Веб-интерфейс
│   ├── app.py
│   ├── templates/
│   └── static/
├── sessions/               # Сессии аккаунтов
└── accounts.json           # Список аккаунтов
```

## 🚀 Использование

### Быстрый старт
```bash
python main.py
```

### Добавление первого аккаунта
1. Откройте Web UI: http://127.0.0.1:8088
2. Добавьте аккаунт через форму или команду:
   ```
   .addaccount main +79990000000
   .connectacc main
   .sendcode main
   .loginacc main 12345
   ```

### Звонок
```
.call 123456789 audio
.call 123456789 video
.activcalls
.endcall <call_id>
```

## 🔧 TODO
- [ ] Уточнить opcode для звонков через сниффинг трафика
- [ ] Добавить обработку входящих звонков (webhook)
- [ ] Реализовать полноценный WebRTC для звонков
- [ ] Добавить команды для управления конкретным аккаунтом в звонках
- [ ] Расширить Web UI страницей управления звонками
