import time
import unittest

from core.security import MagicLinkManager


class MagicLinkManagerTests(unittest.TestCase):
    def test_issue_returns_unique_tokens(self):
        m = MagicLinkManager(ttl_seconds=60)
        a = m.issue()
        b = m.issue()
        self.assertNotEqual(a.token, b.token)
        self.assertGreater(len(a.token), 32)

    def test_redeem_valid_then_used(self):
        m = MagicLinkManager(ttl_seconds=60)
        link = m.issue()
        self.assertTrue(m.redeem(link.token))
        # Повторное redeem отвергается — single-use.
        self.assertFalse(m.redeem(link.token))

    def test_redeem_unknown_token(self):
        m = MagicLinkManager(ttl_seconds=60)
        self.assertFalse(m.redeem("nonexistent"))
        self.assertFalse(m.redeem(""))
        self.assertFalse(m.redeem(None))

    def test_redeem_expired_token(self):
        m = MagicLinkManager(ttl_seconds=60)
        link = m.issue()
        # Принудительно состарим токен.
        m._links[link.token].expires_at = time.time() - 1
        self.assertFalse(m.redeem(link.token))
        # И его не осталось в сторе после неудачного redeem.
        self.assertEqual(m.active_count(), 0)

    def test_active_count_cleans_expired(self):
        m = MagicLinkManager(ttl_seconds=60)
        link_alive = m.issue()
        link_dead = m.issue()
        m._links[link_dead.token].expires_at = time.time() - 1
        self.assertEqual(m.active_count(), 1)
        # Живой токен ещё работает.
        self.assertTrue(m.redeem(link_alive.token))


if __name__ == "__main__":
    unittest.main()
