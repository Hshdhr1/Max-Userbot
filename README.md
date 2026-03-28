<div align="center">
  <img src="https://github.com/hikariatama/assets/raw/master/1326-command-window-line-flat.webp" height="80" alt="MAX Userbot logo">
  <h1>Max Userbot</h1>
  <p>Advanced MAX (OneMe) userbot with module system, multi-account foundations and modern Web UI</p>

  <p>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/platform-MAX%20OneMe-7c3aed" alt="MAX OneMe">
    <img src="https://img.shields.io/badge/ui-web--beta-16a34a" alt="Web UI beta">
    <img src="https://img.shields.io/badge/modules-core%2018-orange" alt="18 core modules">
  </p>
</div>

---

## ⚠️ Security Notice

> Установка модулей из недоверенных источников может повредить аккаунт/сервер.
>
> **Рекомендации:**
> - ✅ Ставь модули только из доверенных репозиториев.
> - ❌ Не запускай подозрительные команды (`!terminal`, `!e`, `!ecpp`, ...).
> - ✅ Делай регулярные бэкапы и включай ограничения API.

---

## 🚀 Возможности

- Встроенная система модулей (18 core модулей, стиль как в Heroku).
- Все core модули защищены от выгрузки/замены.
- Конфиги модулей через сообщения (`!config`, `!fconfig`) + Web UI.
- Мультиаккаунт-фундамент (`accounts.json`) + добавление аккаунтов в Web UI.
- Web UI в тёмном стиле с боковым меню, карточками модулей и панелью конфигов.
- Поддержка reply-загрузки модулей (`!loadmod` reply на `.py`).
- Базовые методы для разработчиков модулей:
  - динамические команды,
  - watcher-ы пакетов,
  - отправка собственных opcode.
- API-расширения:
  - реакции на сообщения,
  - смена имени/фамилии/био,
  - работа с избранным.
- Markdown helper (`!md`) и HTML-safe вывод.
- Защита от случайной отправки в другой чат во время обработки команды.

---

## 📦 Установка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python userbot.py
```

### Первый запуск

1. Введи номер в формате `+79990000000`.
2. Введи SMS-код.
3. Сессия сохранится в `max_session.txt`.

---

## 🧩 Ключевые команды

- `!modules` / `!ml`
- `!help [module|command]`
- `!helphide <module>`
- `!config <module>`
- `!fconfig <module> <key> <value>`
- `!weburl`
- `!addacc`
- `!accounts`
- `!loadmod <modules/file.py>` (или reply на `.py`)
- `!unloadmod <module>`
- `!react <message_id> <emoji>`
- `!setname <first> [last]`
- `!setbio <text>`
- `!setfav <chat_id>`
- `!favsay <text>`
- `!md <text>`

---

## 🌐 Web UI (beta)

Web UI по команде `!weburl`.

Что уже есть:
- сайдбар модулей,
- дашборд статов,
- подключённые аккаунты,
- начало логики мультиаккаунтов,
- форма изменения конфигов модулей.

---

## 🧠 Документация

- Черновик по модульной системе: [`MODULES.md`](MODULES.md)
- 100 идей развития: [`IDEAS.md`](IDEAS.md)
- Примеры плагинов:
  - `modules/examples/echo_plugin.py`
  - `modules/examples/opcode_watcher_plugin.py`

---

## 🛡 Дисклеймер

Проект предоставляется «как есть». Автор не несёт ответственности за:
- блокировки аккаунта,
- утечки сессий,
- последствия запуска вредоносных модулей.

Используй осторожно и в рамках правил платформы.
