<p align="center">
  <img src="https://github.com/Igroshka/Maxli/blob/main/logo.png?raw=true" alt="Maxli Logo" width="150" style="border-radius:50% !important;" />
</p>

<h1 align="center">Max Userbot — UserBot для мессенджера MAX</h1>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/Stars-Welcome-FFC107?style=for-the-badge&logo=github" alt="Stars"></a>
  <a href="#license"><img src="https://img.shields.io/badge/License-Apache--2.0-8BC34A?style=for-the-badge" alt="License"></a>
  <a href="https://UserbotMax.t.me"><img src="https://img.shields.io/badge/Telegram-Новости-blue?style=for-the-badge&logo=telegram" alt="Telegram Channel"></a>
</p>

<p align="center">
  ⚡️ UserBot для <b>MAX (OneMe)</b> с модульной системой, Web UI и базой для мультиаккаунтов.<br>
  Создан для автоматизации, расширяемости и удобной разработки модулей.
</p>

---

## 🚀 О проекте

`Max Userbot` — это Python-проект с упором на:

- модульность (встроенные и внешние модули),
- удобное управление через сообщения,
- Web UI для модулей/конфигов/аккаунтов,
- расширяемый API-слой для opcode-операций.

Базовая библиотека: [`vkmax`](https://pypi.org/project/vkmax/).

---

## ✨ Ключевые возможности

- 🧩 **Система модулей** (core + внешние `.py` модули через `.loadmod`)
- 🔒 **Защита core-модулей** от выгрузки/замены
- ⚙️ **Конфиги модулей** через `.config/.fconfig` и через Web UI
- 🌐 **Web UI (beta)**: модули, конфиги, подключённые аккаунты
- 👥 **Мультиаккаунт-база** (`accounts.json`, старт логики add account)
- 😀 **Реакции через API** (`.react`)
- 👤 **Смена профиля** (`.setname`, `.setbio`)
- ⭐ **Работа с избранным** (`.setfav`, `.favsay`)
- 📝 **Markdown helper** (`.md`) + безопасный HTML-вывод
- 🛡 **Guard от случайной отправки** не в тот чат во время обработки
- 🔁 **Совместимость префиксов:** стандартный `.` + поддержка `!`

---

## 📋 Требования

| Требование | Версия |
|------------|--------|
| Python     | 3.10+  |
| pip        | Любая  |
| Git        | Любая  |

Проверка:

```bash
python --version
pip --version
git --version
```

---

## 🛠️ Установка

### Linux / macOS

```bash
git clone <YOUR_REPO_URL>
cd Max-Userbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python userbot.py
```

### Windows (PowerShell)

```powershell
git clone <YOUR_REPO_URL>
cd Max-Userbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python userbot.py
```

### Первый запуск

1. Введите номер в формате `+79001234567`
2. Введите SMS-код из MAX
3. Сессия сохранится в `max_session.txt`

---

## 🔄 Повторный запуск

### Linux / macOS

```bash
cd Max-Userbot
source .venv/bin/activate
python userbot.py
```

### Windows

```powershell
cd Max-Userbot
.venv\Scripts\activate
python userbot.py
```

---

## 🧩 Основные команды

- `.modules` / `.ml`
- `.help [module|command]`
- `.helphide <module>`
- `.config <module>`
- `.fconfig <module> <key> <value>`
- `.weburl`
- `.addacc`
- `.accounts`
- `.loadmod <modules/file.py>` (или reply на `.py`)
- `.unloadmod <module>`
- `.react <message_id> <emoji>`
- `.setname <first> [last]`
- `.setbio <text>`
- `.setfav <chat_id>`
- `.favsay <text>`
- `.md <text>`

> Также поддерживаются команды с `!`, но стандартный префикс — `.`

---

## 🌐 Web UI

Запуск через команду `.weburl`.

Что уже есть:

- sidebar с модулями,
- dashboard карточки (клиенты/модули/uptime),
- список подключённых аккаунтов,
- форма добавления аккаунта,
- форма редактирования модульных конфигов.

---

## 📁 Структура проекта

```text
Max-Userbot/
├── userbot.py
├── requirements.txt
├── README.md
├── MODULES.md
├── IDEAS.md
└── modules/
    └── examples/
        ├── echo_plugin.py
        └── opcode_watcher_plugin.py
```

---

## 🧠 Документация

- Модульная система: [`MODULES.md`](MODULES.md)
- 100 идей развития: [`IDEAS.md`](IDEAS.md)
- Примеры модулей:
  - `modules/examples/echo_plugin.py`
  - `modules/examples/opcode_watcher_plugin.py`

---

## ❓ FAQ

<details>
<summary><b>Как войти заново (сбросить сессию)?</b></summary>

Удалите `max_session.txt` и перезапустите `python userbot.py`.

</details>

<details>
<summary><b>Почему не работает Web UI?</b></summary>

Проверьте порт `8088`, и что команда `.weburl` была выполнена после запуска бота.

</details>

<details>
<summary><b>Можно ли писать свои модули?</b></summary>

Да, через `setup(registry)` + `register_module` / `register_dynamic_command` / `register_watcher`.

</details>

---

## ⚠️ Security Notice

- Не устанавливайте модули из неизвестных источников.
- Будьте осторожны с мощными командами (`.terminal`, `.e`, `.ecpp`, ...).
- Не публикуйте `max_session.txt` и токены.

---

## 📜 License

Apache License 2.0.

---

<p align="center">
  <b>⭐ Если проект полезен — поставьте звезду!</b>
</p>
