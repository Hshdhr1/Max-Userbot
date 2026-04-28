"""Пароль и сессии для опасных действий.

- Хеш паролей через `hashlib.scrypt` (stdlib, без внешних зависимостей).
- Сессии с TTL держатся в памяти процесса (`SessionManager`). При рестарте
  бот снова потребует unlock — это намеренно.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger("max-userbot.security")

# Параметры scrypt — взвешены так, чтобы хеш считался ~150ms на дешёвом VPS.
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def hash_password(password: str) -> tuple[str, str]:
    """Возвращает (hex hash, hex salt). Salt генерируется случайно."""
    if not isinstance(password, str) or not password:
        raise ValueError("password must be non-empty string")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_BYTES,
    )
    return digest.hex(), salt.hex()


def verify_password(password: str, hex_hash: str, hex_salt: str) -> bool:
    """Проверяет пароль; возвращает False при любых нештатных данных."""
    if not password or not hex_hash or not hex_salt:
        return False
    try:
        salt = bytes.fromhex(hex_salt)
        expected = bytes.fromhex(hex_hash)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
    except (ValueError, MemoryError) as exc:
        logger.debug("verify_password failed: %s", exc)
        return False
    return hmac.compare_digest(digest, expected)


# ----------------------------- session manager ------------------------------


@dataclass
class _Session:
    token: str
    issued_at: float
    expires_at: float
    label: str = "default"


class SessionManager:
    """In-memory store коротких токенов unlock-сессий."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}

    def create(self, label: str = "default") -> _Session:
        """Создаёт сессию и возвращает её. Токен — 32 байта hex."""
        token = secrets.token_hex(32)
        now = time.time()
        session = _Session(
            token=token,
            issued_at=now,
            expires_at=now + self.ttl,
            label=label,
        )
        with self._lock:
            self._sessions[token] = session
        return session

    def is_valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return False
            if session.expires_at < time.time():
                self._sessions.pop(token, None)
                return False
        return True

    def revoke(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            return self._sessions.pop(token, None) is not None

    def revoke_all(self) -> int:
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
        return n

    def cleanup(self) -> int:
        """Удалить просроченные. Возвращает их количество."""
        now = time.time()
        with self._lock:
            expired = [t for t, s in self._sessions.items() if s.expires_at < now]
            for t in expired:
                self._sessions.pop(t, None)
        return len(expired)

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


# Глобальные синглтоны.
session_manager = SessionManager(ttl_seconds=int(os.getenv("MAX_UNLOCK_TTL", "600")))


# ------------------------------- dangerous list ------------------------------


DANGEROUS_COMMANDS: set[str] = {
    # Произвольное выполнение кода / shell.
    "eval",
    "exec",
    "terminal",
    "shell",
    "sh",
    # Команды, которые НЕМЕДЛЕННО исполняют .py-файл в текущем
    # процессе (`spec.loader.exec_module`). dlm ещё и скачивает с сети.
    # Это эквивалент eval, их оставляем в dangerous.
    "loadmod",
    "lm",
    "dlm",
    "dlmod",
    # Аккаунты — могут привести к угону сессии и разлогину.
    "addaccount",
    "loginacc",
    "deleteaccount",
    "delaccount",
    "removeaccount",
    "lock",
    "unlock",  # сама команда unlock не должна вечно требовать unlock'а — обработана отдельно
}

# Установка из каталога и выгрузка из реестра сами по себе НЕ опасны:
# `installmod`/`uninstallmod` только пишут/удаляют .py-файлы в `modules/`,
# файл проходит `core.threat_scan` и исполняется только после явного
# перезапуска. `unloadmod`/`ulm` выкидывает из реестра, не исполняя код.
# Этот список справочный, не используется как gate.
SAFE_BUT_RECENTLY_DANGEROUS: set[str] = {
    "installmod",
    "uninstallmod",
    "rmmod",
    "unloadmod",
    "ulm",
}


def is_dangerous(command: str) -> bool:
    return command.lower() in DANGEROUS_COMMANDS


__all__ = [
    "hash_password",
    "verify_password",
    "SessionManager",
    "session_manager",
    "is_dangerous",
    "DANGEROUS_COMMANDS",
]
