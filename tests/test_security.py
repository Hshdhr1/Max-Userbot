"""Тесты для core.security — пароли + sessions."""

import time
import unittest

from core.security import (
    DANGEROUS_COMMANDS,
    SessionManager,
    hash_password,
    is_dangerous,
    verify_password,
)


class HashPasswordTests(unittest.TestCase):
    def test_hash_and_verify_roundtrip(self):
        h, s = hash_password("hunter2")
        self.assertTrue(h)
        self.assertTrue(s)
        self.assertTrue(verify_password("hunter2", h, s))
        self.assertFalse(verify_password("wrong", h, s))

    def test_distinct_salts(self):
        a_hash, a_salt = hash_password("samepass")
        b_hash, b_salt = hash_password("samepass")
        # Соли разные => хеши разные.
        self.assertNotEqual(a_salt, b_salt)
        self.assertNotEqual(a_hash, b_hash)

    def test_empty_password_rejected(self):
        with self.assertRaises(ValueError):
            hash_password("")
        self.assertFalse(verify_password("", "deadbeef", "cafebabe"))

    def test_corrupt_inputs_dont_throw(self):
        self.assertFalse(verify_password("any", "not-hex", "not-hex"))
        self.assertFalse(verify_password("any", "", ""))


class SessionManagerTests(unittest.TestCase):
    def test_create_and_validate(self):
        mgr = SessionManager(ttl_seconds=60)
        s = mgr.create()
        self.assertEqual(len(s.token), 64)
        self.assertTrue(mgr.is_valid(s.token))
        self.assertEqual(mgr.active_count(), 1)

    def test_revoke(self):
        mgr = SessionManager(ttl_seconds=60)
        s = mgr.create()
        self.assertTrue(mgr.revoke(s.token))
        self.assertFalse(mgr.is_valid(s.token))
        self.assertEqual(mgr.active_count(), 0)

    def test_expired(self):
        mgr = SessionManager(ttl_seconds=1)
        s = mgr.create()
        # Подменяем expires в прошлое.
        with mgr._lock:
            mgr._sessions[s.token].expires_at = time.time() - 10
        self.assertFalse(mgr.is_valid(s.token))
        self.assertEqual(mgr.active_count(), 0)

    def test_revoke_all(self):
        mgr = SessionManager()
        for _ in range(3):
            mgr.create()
        self.assertEqual(mgr.revoke_all(), 3)
        self.assertEqual(mgr.active_count(), 0)


class DangerousFlagTests(unittest.TestCase):
    def test_known_dangerous(self):
        # Реально опасное: исполнение кода, shell, аккаунты.
        for cmd in ("eval", "exec", "terminal", "shell", "addaccount", "loginacc"):
            self.assertTrue(is_dangerous(cmd), cmd)
            self.assertTrue(is_dangerous(cmd.upper()))

    def test_module_loading_is_not_dangerous(self):
        # Установка/выгрузка модуля сама по себе не опасна. Опасным
        # считается только то, что модуль внутри себя пытается сделать —
        # это ловит core.threat_scan на статическом анализе.
        for cmd in ("lm", "dlm", "dlmod", "loadmod", "ulm", "unloadmod",
                    "installmod", "uninstallmod", "rmmod"):
            self.assertFalse(is_dangerous(cmd), cmd)

    def test_unknown_safe(self):
        for cmd in ("ping", "modules", "help", "config"):
            self.assertFalse(is_dangerous(cmd), cmd)

    def test_set_is_consistent(self):
        # Защищаемся от случайных регрессий.
        self.assertIn("eval", DANGEROUS_COMMANDS)
        self.assertIn("terminal", DANGEROUS_COMMANDS)
        self.assertNotIn("loadmod", DANGEROUS_COMMANDS)
        self.assertNotIn("lm", DANGEROUS_COMMANDS)


if __name__ == "__main__":
    unittest.main()
