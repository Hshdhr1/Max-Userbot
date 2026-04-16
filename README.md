<p align="center">
  <img src="https://github.com/Igroshka/Maxli/blob/main/logo.png?raw=true" alt="Maxli Logo" width="150" style="border-radius:50% !important;" />
</p>

<h1 align="center">Max Userbot — UserBot для мессенджера "Max"</h1>

<p align="center">
  <a href="#"><img src="https://img.shields.io/badge/stars-welcome-FFC107?style=for-the-badge&logo=github" alt="Stars"></a>
  <a href="#"><img src="https://img.shields.io/badge/license-Apache%202.0-8BC34A?style=for-the-badge" alt="License"></a>
  <a href="https://UserbotMax.t.me"><img src="https://img.shields.io/badge/Telegram-Новости-blue?style=for-the-badge&logo=telegram" alt="Telegram Channel"></a>
</p>

<p align="center">
  ⚡️ UserBot для мессенджера <b>Max</b> с гибкой системой модулей и Web UI.<br>
  База для автоматизации, плагинов и расширения возможностей клиента.
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/Igroshka/Maxli/refs/heads/main/banner1.png" alt="Maxli Banner" width="100%" style="border-radius:5% !important;" />
</p>

---

## 🚀 О проекте

Max Userbot — это Python-проект для MAX (OneMe), ориентированный на:

- автоматизацию рутинных действий,
- расширение функционала через модули,
- удобное управление через команды и Web UI,
- дальнейшее развитие мультиаккаунтов и API-операций.

Проект использует библиотеку [`vkmax`](https://pypi.org/project/vkmax/) для работы с протоколом MAX.

---

## ✨ Ключевые возможности

- 🤖 **Автоматизация:** команды, обработчики, watcher-ы пакетов
- 🧩 **Гибкая система модулей:** core + внешние плагины (`.loadmod`, `.dlm <url>`)
- 💬 **Поддержка чатов:** работа в личках и чатах
- 🌐 **Web UI:** документация модулей, управление конфигами, аккаунты
- ⚙️ **Конфигурация:** `.config`, `.fconfig` + Web UI (синхронизация)
- 👥 **Мультиаккаунт:** `accounts.json` + Web UI + команды управления
- 😀 **API-функции:** реакции, смена профиля, звонки, raw opcode
- 📞 **Звонки:** команды для управления вызовами (opcode 200-203)

---

## 📢 Новости и поддержка

➡️ Канал новостей: [UserbotMax.t.me](https://UserbotMax.t.me)

---

## 📋 Требования

| Требование | Минимальная версия | Как проверить |
|------------|-------------------|---------------|
| Python | 3.10+ | `python --version` |
| pip | Любая | `pip --version` |
| Git | Любая | `git --version` |

### Установка Python

<details>
<summary><b>🖥️ Windows</b></summary>

1. Скачайте Python: https://www.python.org/downloads/
2. При установке включите ✅ `Add Python to PATH`
3. После установки проверьте: `python --version`

</details>

<details>
<summary><b>🐧 Linux (Ubuntu/Debian)</b></summary>

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip git -y
```

</details>

<details>
<summary><b>🍎 macOS</b></summary>

```bash
brew install python git
```

</details>

---

## 🛠️ Установка

### 🖥️ Windows (PowerShell)

<details>
<summary><b>⌨️ Ручная установка</b></summary>

```powershell
# 1. Клонируем репозиторий
git clone <YOUR_REPO_URL>

# 2. Переходим в папку проекта
cd Max-Userbot

# 3. Создаем виртуальное окружение
python -m venv .venv

# 4. Активируем
.venv\Scripts\activate

# 5. Ставим зависимости
pip install -r requirements.txt

# 6. Запускаем
python main.py
```

</details>

### 🐧 Linux / macOS

<details>
<summary><b>⌨️ Ручная установка</b></summary>

```bash
# 1. Клонируем репозиторий
git clone <YOUR_REPO_URL>

# 2. Переходим в папку
cd Max-Userbot

# 3. Создаём venv
python3 -m venv .venv

# 4. Активируем
source .venv/bin/activate

# 5. Устанавливаем зависимости
pip install -r requirements.txt

# 6. Запускаем
python main.py
```

</details>

### Первый запуск

- Введите номер `+79001234567`
- Введите SMS-код из MAX
- Сессия сохранится в `max_session.txt`

---

## 🔄 Повторный запуск

**Windows (PowerShell):**
```powershell
cd C:\path\to\Max-Userbot
.venv\Scripts\activate
python main.py
```

**Linux/macOS:**
```bash
cd ~/Max-Userbot
source .venv/bin/activate
python main.py
```

---

## 🔄 Автозапуск

<details>
<summary><b>🖥️ Windows — Task Scheduler</b></summary>

1. Создайте `.bat` файл с активацией `.venv` и запуском `python main.py`
2. Добавьте его в Task Scheduler как задачу "At logon"

</details>

<details>
<summary><b>🐧 Linux — systemd</b></summary>

Пример unit-файла:

```ini
[Unit]
Description=Max Userbot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/USER/Max-Userbot
ExecStart=/home/USER/Max-Userbot/.venv/bin/python /home/USER/Max-Userbot/main.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Дальше:

```bash
sudo cp max-userbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable max-userbot
sudo systemctl start max-userbot
```

</details>

---

## 📁 Структура проекта

```text
Max-Userbot/
├── main.py                       # ▶️ Основной файл для запуска
├── userbot.py                    # Runtime (до полного рефакторинга)
├── core/                         # ⚙️ Ядро и ключевые компоненты
│   ├── api.py                    # API-обёртка
│   ├── config.py                 # Конфигурация
│   └── loader.py                 # Загрузчик модулей
├── core_modules/                 # 🔧 Встроенные модули (целевая директория)
├── webui/                        # 🌐 Web UI пакет
│   ├── app.py
│   └── README.md
├── mods/                         # 📚 Документация для разработчиков модулей
│   ├── modules.md
│   └── llms.txt
├── modules/
│   └── examples/
│       ├── echo_plugin.py
│       └── opcode_watcher_plugin.py
├── install_windows.bat
├── install_linux.sh
├── requirements.txt
├── MODULES.md
├── IDEAS.md
└── README.md
```

---

## 🧩 Создание модулей

См. [`MODULES.md`](MODULES.md) и примеры в `modules/examples/`.

Минимальный шаблон:

```python
from userbot import BotModule, ModuleCommand

def setup(registry):
    registry.register_module(
        BotModule(
            name="MyModule",
            description="Demo module",
            commands=[ModuleCommand(name="hello", description="Say hello")],
            builtin=False,
        )
    )
```

---

## 📚 Документация

- **[WEBUI_FEATURES.md](WEBUI_FEATURES.md)** — документация по Web UI: управление конфигами, документация модулей
- **[MODULES.md](MODULES.md)** — руководство по созданию модулей
- **[CHANGES.md](CHANGES.md)** — история изменений проекта
- **[IDEAS.md](IDEAS.md)** — планы развития и идеи

---

## ❓ Частые вопросы

<details>
<summary><b>Где взять код подтверждения?</b></summary>

Код приходит в MAX после отправки номера телефона.

</details>

<details>
<summary><b>Бот не запускается, что делать?</b></summary>

1. Проверьте Python: `python --version`
2. Убедитесь, что venv активирован
3. Переустановите зависимости: `pip install -r requirements.txt --force-reinstall`

</details>

<details>
<summary><b>Как войти заново?</b></summary>

Удалите `max_session.txt` и запустите `python main.py`.

</details>

---

## 📜 License

Apache License 2.0

---

<p align="center">
  <b>⭐ Если проект полезен — поставьте звезду на GitHub!</b>
</p>
