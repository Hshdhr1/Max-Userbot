# Max (vkmax) — справочник по протоколу

Эта страница — компактный референс WebSocket-протокола мессенджера Max,
который используется текущей SDK `vkmax`. Полезна авторам модулей, когда
готовых high-level методов в `vkmax` не хватает и нужно отправить пакет
напрямую через `client.send_raw(...)`.

> Альтернатива: библиотека [PyMax](https://pymax.org) (`pip install
> maxapi-python`). Это **отдельный** SDK от MaxApiTeam, не связанный с
> `vkmax`. Если в твоей задаче нужны фичи, которых нет в `vkmax` (напр.,
> загрузка файлов как attachment), PyMax — рабочая альтернатива. Но в
> этой репе в качестве основной SDK сейчас используется именно `vkmax`.

## WebSocket endpoint

```
wss://ws-api.oneme.ru/websocket
```

Все пакеты — JSON. Поля:

| Поле     | Тип          | Описание |
|----------|--------------|----------|
| `ver`    | int          | Версия протокола, сейчас `11` |
| `seq`    | int          | Инкрементный ID пакета. Ответ имеет тот же `seq`, что и запрос |
| `cmd`    | int (0\|1)   | `0` для исходящих, `1` для входящих |
| `opcode` | int          | Идентификатор RPC-метода, см. таблицу ниже |
| `payload`| object       | Произвольный JSON, зависит от opcode |

## Auth flow

1. **Connect** к `wss://ws-api.oneme.ru/websocket`.
2. **Hello** — userAgent-пакет, описывает клиент:
   ```json
   {
     "userAgent": {
       "deviceType": "WEB",
       "locale": "ru_RU",
       "osVersion": "macOS",
       "deviceName": "vkmax Python",
       "headerUserAgent": "Mozilla/5.0 ...",
       "deviceLocale": "ru-RU",
       "appVersion": "<APP_VERSION>",
       "screen": "956x1470 2.0x",
       "timezone": "Asia/Vladivostok"
     },
     "deviceId": "<uuid4>"
   }
   ```
3. **Start auth** (opcode `17`): запрашиваем SMS:
   ```json
   {"ver":11,"cmd":0,"seq":1,"opcode":17,
    "payload":{"phone":"<phone>","type":"START_AUTH","language":"ru"}}
   ```
   В ответ приходит `sms_token`.
4. **Confirm code**:
   ```json
   {"token":"<sms_token>","verifyCode":"<code>","authTokenType":"CHECK_CODE"}
   ```
5. **Login by token** (opcode `19`) — для повторного входа существующей сессии:
   ```json
   {"ver":11,"cmd":0,"seq":1,"opcode":19,
    "payload":{"interactive":true,"token":"<token>","chatsSync":0,
               "contactsSync":0,"presenceSync":0,"draftsSync":0,"chatsCount":40}}
   ```

## Opcode reference

### Сообщения

| Opcode | Действие | Payload |
|--------|----------|---------|
| `64`   | Отправить сообщение | `{"chatId":<id>,"message":{"text":"...","cid":<unix_ms>,"elements":[],"attaches":[]},"notify":true}` |
| `64`   | Отправить стикер   | `{"chatId":<id>,"message":{"cid":<unix_ms>,"attaches":[{"_type":"STICKER","stickerId":<id>}]},"notify":true}` |
| `66`   | Удалить сообщение  | `{"chatId":<id>,"messageIds":["<msgId>"],"forMe":false}` |
| `67`   | Редактировать      | `{"chatId":<id>,"messageId":"<msgId>","text":"...","elements":[],"attachments":[]}` |
| `178`  | Реакция            | `{"chatId":<id>,"messageId":"<msgId>","reaction":{"reactionType":"EMOJI","id":"❤️"}}` |
| `128`  | Incoming push      | (входящее уведомление о новом / отредактированном / удалённом сообщении) |

### Чаты

| Opcode | Действие | Payload |
|--------|----------|---------|
| `49`   | Получить историю чата   | `{"chatId":<id>,"from":<unix_ms>,"forward":0,"backward":30,"getMessages":true}` |
| `50`   | Отметить как прочитанное | `{"type":"READ_MESSAGE","chatId":<id>,"messageId":"<msgId>","mark":<unix_ms>}` |
| `50`   | Сделать непрочитанным    | `{"type":"SET_AS_UNREAD","chatId":<id>,"mark":<unix_ms>}` |
| `57`   | Войти / подписаться по ссылке | `{"link":"https://max.ru/<slug>"}` |
| `75`   | Покинуть канал/чат           | `{"chatId":<id>,"subscribe":false}` |
| `77`   | Добавить пользователей в чат | `{"chatId":<id>,"userIds":[<id>,...],"showHistory":true,"operation":"add"}` |
| `22`   | Mute навсегда                | `{"settings":{"chats":{"<id>":{"dontDisturbUntil":-1}}}}` |
| `22`   | Снять mute                   | `{"settings":{"chats":{"<id>":{"dontDisturbUntil":0}}}}` |

### Контакты и профиль

| Opcode | Действие | Payload |
|--------|----------|---------|
| `32`   | Получить контакты           | `{"contactIds":[<id>,<id>,...]}` |
| `34`   | Добавить в контакты         | `{"contactId":<id>,"action":"ADD"}` |
| `22`   | Скрыть профиль (HIDDEN=true)| `{"settings":{"user":{"HIDDEN":true}}}` |

## Поле `cid` в сообщениях

`cid` — клиентский ID сообщения, **обязательный** при отправке. Обычно
ставится `int(time.time() * 1000)` или unique nonce. Сервер использует
его для дедупликации повторных отправок.

## Использование из модуля

```python
from core import loader

@loader.tds
class MyMod(loader.Module):
    async def client_ready(self, client, db):
        self.client = client

    @loader.command(ru_doc="<emoji> [<msgId>] — поставить реакцию")
    async def react(self, message):
        # Прямой raw-вызов opcode 178.
        await self.client.send_raw({
            "ver": 11, "cmd": 0, "seq": 0, "opcode": 178,
            "payload": {
                "chatId": message.chat_id,
                "messageId": str(message.id),
                "reaction": {"reactionType": "EMOJI", "id": "❤️"},
            },
        })
```

> Высокоуровневые обёртки (типа `client.send_message`) уже инкапсулируют
> часть opcode'ов. Используй raw-API только когда нужного метода нет.

## Push-сообщения (opcode 128)

Это **входящий** opcode — пакет с `cmd=1` приходит на твой колбэк
`MultiAccountManager.set_default_callback(...)`. Ключевые поля payload:

```json
{
  "opcode": 128,
  "payload": {
    "chatId": <id>,
    "message": {
      "id": <msgId>,
      "text": "...",
      "status": "EDITED" | "REMOVED" | null,
      "sender": <user_id>,
      ...
    }
  }
}
```

`status: "EDITED"` означает, что сообщение редактировали; `"REMOVED"` —
удалили. На этом построен модуль `EditTracker` в `modules/examples/`.

## Дополнительно

- `seq=0` допустим, если ответ нам не нужен; для парных request/response
  лучше использовать монотонно растущий счётчик.
- Все числовые ID (`chatId`, `userId`, `messageId`) — 64-битные.
- Сервер шлёт keepalive ping каждые ~30 сек, ответ обязателен (vkmax
  делает это сам).

## Источники

- Файлы `protocol.md` и `opcodes.md` в issue репозитория.
- vkmax — текущая SDK, [pypi.org/project/vkmax](https://pypi.org/project/vkmax/).
- pymax — альтернативный SDK от MaxApiTeam, [pymax.org](https://pymax.org/).
