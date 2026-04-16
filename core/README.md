# Core API

API-обёртка над runtime объектами Max Userbot. Содержит `MaxApiExtensions` для работы с raw opcode и `CoreAPI` для унифицированного доступа.

## Функционал

- **send_raw(opcode, payload)** - Отправка произвольного opcode
- **react(chat_id, message_id, emoji)** - Отправка реакции
- **update_profile(first_name, last_name, bio)** - Обновление профиля
- **start_call(chat_id, user_id, video)** - Начало звонка (opcode 200)
- **accept_call(call_id)** - Принятие звонка (opcode 201)
- **end_call(call_id)** - Завершение звонка (opcode 202)
- **reject_call(call_id)** - Отклонение звонка (opcode 203)

---

# Client Manager (MultiAccount)

Менеджер множественных аккаунтов. Позволяет работать с несколькими аккаунтами одновременно.

## Классы

### MultiAccountManager
- `add_account(label, phone)` - Добавить аккаунт
- `remove_account(label)` - Удалить аккаунт
- `connect_account(label)` - Подключить аккаунт
- `disconnect_account(label)` - Отключить аккаунт
- `send_code(label)` - Отправить SMS код
- `login_by_sms(label, sms_code)` - Войти по SMS
- `get_account(label)` - Получить активный аккаунт
- `get_all_accounts()` - Список всех активных аккаунтов
- `connect_all()` - Подключить все аккаунты
- `disconnect_all()` - Отключить все аккаунты

### ActiveAccount
Активный аккаунт с клиентом:
- `label` - Метка аккаунта
- `phone` - Номер телефона
- `client` - MaxClient экземпляр
- `api` - MaxApiExtensions
- `authorized` - Статус авторизации
- `callback` - Обработчик пакетов

---

# Call Manager

Менеджер звонков. Предоставляет API для управления аудио/видео звонками.

## Классы

### CallManager
- `set_api(api)` - Установка API экземпляра
- `start_call(chat_id, user_id, video)` - Начать звонок
- `accept_call(call_id)` - Принять звонок
- `end_call(call_id)` - Завершить звонок
- `reject_call(call_id)` - Отклонить звонок
- `get_active_calls()` - Список активных звонков
- `register_handler(handler)` - Регистрация обработчика событий

### CallInfo
Информация о звонке:
- `call_id` - ID звонка
- `chat_id` - ID чата
- `caller_id` - ID звонящего
- `callee_id` - ID принимающего
- `status` - Статус (ringing, connected, ended)
- `type` - Тип (audio, video)
- `duration` - Длительность в секундах

> ⚠️ **Важно**: Opcode для звонков (200-203) являются предположительными. Необходимо уточнить актуальные значения в документации vkmax или через сниффинг трафика.

---

# Loader

Загрузчик модулей с поддержкой загрузки из файла и URL.

## Классы

### ModuleManager
- `ensure_modules_dir(path)` - Создать директорию модулей
- `load_module_from_path(path, modules_dir)` - Загрузить из файла
- `load_module_from_url(url, modules_dir)` - Загрузить из URL
- `unload_module(name)` - Выгрузить модуль

---

# Модули

## Calls (core_modules/calls.py)
Модуль управления звонками.

### Команды:
- `.call <user_id> [audio|video]` - Начать звонок
- `.acceptcall <call_id>` - Принять звонок
- `.endcall <call_id>` - Завершить звонок
- `.rejectcall <call_id>` - Отклонить звонок
- `.activcalls` - Показать активные звонки

## MultiAccount (core_modules/multiaccount.py)
Модуль управления мультиаккаунтами.

### Команды:
- `.addaccount <label> <phone>` - Добавить аккаунт
- `.connectacc <label>` - Подключить аккаунт
- `.disconnectacc <label>` - Отключить аккаунт
- `.listacc` - Список всех аккаунтов
- `.sendcode <label>` - Отправить SMS код
- `.loginacc <label> <code>` - Войти по SMS коду
- `.removeacc <label>` - Удалить аккаунт

---

# Пример использования

```python
from core.multiaccount import multiaccount_manager
from core.client_manager import call_manager
from core.api import MaxApiExtensions

# Добавление и подключение аккаунта
multiaccount_manager.add_account("main", "+79990000000")
active = await multiaccount_manager.connect_account("main")

# Авторизация через SMS
await multiaccount_manager.send_code("main")
# ... получить код от пользователя ...
await multiaccount_manager.login_by_sms("main", 12345)

# Настройка обработчика пакетов
async def on_packet(client, packet):
    # обработка пакетов
    pass

multiaccount_manager.set_callback("main", on_packet)

# Использование звонков
call_manager.set_api(active.api)
result = await call_manager.start_call(
    chat_id=123456,
    user_id=789012,
    video=True
)
```
