# Установка Max Userbot на Termux (Android)

Гайд проверен на Android 10+ и Termux **0.118.x** (та версия, которая ставится из
[F-Droid](https://f-droid.org/packages/com.termux/) — **не** ставьте Termux из
Google Play, она устаревшая и обновления зависимостей часто ломают сборки).

## 1. Подготовка Termux

```bash
# чистый apt и базовые пакеты
pkg update -y && pkg upgrade -y
pkg install -y python git rust libffi openssl clang make
python -m pip install --upgrade pip
```

`rust + libffi + clang` нужны для сборки `cryptography` / `aiohttp` под termux —
готовых wheel'ов под `aarch64-linux-android` обычно нет, и pip собирает их
локально. На слабом телефоне сборка `cryptography` может занять до 5 минут —
это нормально.

Опционально, чтобы автоматизировать запуск и не держать терминал открытым:

```bash
pkg install -y termux-services tmux
```

## 2. Клонирование и установка зависимостей

```bash
cd ~
git clone https://github.com/Hshdhr1/Max-Userbot.git
cd Max-Userbot
pip install -r requirements.txt
```

Если `pip install` падает на `cryptography` или `lxml` — скорее всего не хватает
системных заголовков. Стандартное решение:

```bash
pkg install -y libxml2 libxslt zlib
CARGO_BUILD_JOBS=2 pip install -r requirements.txt
```

`CARGO_BUILD_JOBS=2` ограничивает количество параллельных rustc-процессов —
4‑ядерный телефон с 4 ГБ ОЗУ при `-j8` упирается в OOM-killer.

## 3. Первый запуск

```bash
python main.py
```

При первом запуске бот спросит:

```
=========================================================
  Установите пароль для опасных действий
  (eval/terminal/.dlm/install/uninstall/addaccount).
  Введите пустую строку, чтобы пропустить — но тогда
  опасные действия будут разрешены без подтверждения.
=========================================================
Пароль:
Повторите пароль:
```

**Используйте достаточно длинный пароль** — он защищает `.eval`, `.terminal`,
`.dlm`, установку и удаление модулей, добавление аккаунтов. Хеш (`scrypt`)
сохранится в `userbot_config.json`. Поменять можно через
`MAX_DANGEROUS_PASSWORD=новый_пароль python main.py` (один раз) или просто
почистить поле в JSON и запустить заново.

После пароля бот будет логиниться; если аккаунтов нет — появится подсказка:

```
Добавьте аккаунт командой: .addaccount <label> <phone>
Затем подключите: .connectacc <label>
И отправьте SMS код: .sendcode <label>
И войдите: .loginacc <label> <code>
```

## 4. Доступ к Web UI

Web UI слушает `127.0.0.1:8088` (изменяется через `MAX_WEBUI_PORT`).
На самом телефоне открывается напрямую:

```bash
termux-open-url http://127.0.0.1:8088
```

(требует `pkg install termux-api` + установленное Termux:API из F-Droid).

С другого устройства в той же Wi-Fi сети — слушайте на 0.0.0.0:

```bash
MAX_WEBUI_HOST=0.0.0.0 MAX_WEBUI_PORT=8088 python main.py
# открывается на ноутбуке как http://<IP-телефона>:8088
```

> ⚠️ В этом случае Web UI доступен всем в сети — **обязательно** установите
> пароль для опасных действий (см. п.3), без него любой может ставить модули
> и добавлять аккаунты.

Если телефон за NAT и нужно зайти из Интернета — пробросьте порт через
[cloudflared](https://github.com/cloudflare/cloudflared/releases) (есть
`cloudflared-linux-arm64`):

```bash
./cloudflared tunnel --url http://localhost:8088
```

или классический `ssh -R`:

```bash
ssh -R 8088:127.0.0.1:8088 user@your-server.example
```

## 5. Запуск в фоне

Termux умеет держать процесс живым только пока активна сессия (или включён
**Acquire Wakelock** в шторке Termux). Самый надёжный способ — `tmux`:

```bash
tmux new -s userbot
python main.py
# Ctrl+B, D — отсоединиться. tmux attach -t userbot — вернуться.
```

Альтернативно — `nohup` + перенаправление логов:

```bash
nohup python main.py > userbot.log 2>&1 &
disown
```

Чтобы стартовать автоматически при загрузке Termux, создайте
`~/.termux/boot/userbot.sh` (нужен пакет Termux:Boot из F-Droid):

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/Max-Userbot
exec python main.py >> userbot.log 2>&1
```

`chmod +x ~/.termux/boot/userbot.sh`. После следующей перезагрузки телефона бот
поднимется сам.

## 6. Каталог модулей и сканер безопасности

В Web UI есть две новые секции:

- **Каталог модулей** — список доступных модулей (по умолчанию из репо), кнопки
  Install/Uninstall. Команды Telegram: `.catalog`, `.installmod <name>`,
  `.uninstallmod <name>`. Все три — опасные, требуют `.unlock`.
- **Безопасность модулей** — статический анализатор сканирует `modules/*.py`
  на типичные угрозы: `fallocate -l 19G`, `dd if=/dev/zero`, `rm -rf /`,
  `subprocess.run(... shell=True)`, `eval(user_input)`, `pickle.loads`,
  `curl … | sh`, чтение `/etc/shadow` или `~/.ssh/id_rsa` и т.п. Telegram-
  команда: `.threats` (только проблемные) или `.scanmod` (все, включая
  чистые).

Сканер не заменяет sandbox — он первый эшелон, фильтрующий совсем уж явные
вещи. Перед `.installmod` для модулей с неизвестных URL'ов всё равно стоит
читать код.

## 7. Что делать, если…

| Симптом                                                  | Решение                                                                                       |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `error: command 'cargo' failed with exit code 137`       | Не хватает RAM. `pkg install proot && proot -0` или собирать с `CARGO_BUILD_JOBS=1`.          |
| `OSError: [Errno 13] Permission denied: 'modules/'`      | Termux не имеет доступа к `/sdcard`. Запускайте из `~/Max-Userbot`, не из `~/storage/`.       |
| `aiohttp` собирается часами                              | Установите `pkg install python-aiohttp` (готовый bin-пакет).                                  |
| Web UI не открывается через `termux-open-url`            | Поставьте Termux:API из F-Droid и `pkg install termux-api`. Или просто откройте URL в Chrome. |
| Бот падает с `SIGTERM` через 30 секунд                   | Android Doze. Включите Termux Wake-lock (`termux-wake-lock`) и отключите оптимизацию батареи. |
| `.eval` / `.terminal` отвечают «🔒 Команда требует unlock» | Сначала `.unlock <ваш пароль>` (см. п.3).                                                     |

## 8. Обновление

```bash
cd ~/Max-Userbot
git pull
pip install -r requirements.txt --upgrade
# Если был запущен через nohup/tmux — перезапустите сессию
```

Конфиги (`userbot_config.json`, `accounts.json`, `userbot_db.json`) при
обновлении не трогаются.

## 9. Удаление

```bash
cd ~ && rm -rf Max-Userbot
# и опционально
pkg uninstall python rust openssl clang
```

---

Если что-то не работает — заведите issue в
<https://github.com/Hshdhr1/Max-Userbot/issues> с приложенным выводом
`pkg list-installed | head -50` и `python -V`.
