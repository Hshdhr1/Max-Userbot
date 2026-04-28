import unittest

from core import telemetry


def _sample_payload(**overrides):
    base = telemetry.build_payload(
        anon_id="abc123" * 10,
        version="max-userbot/test",
        uptime=42,
        modules_count=3,
        commands_count=10,
        watchers_count=2,
        accounts_total=1,
        accounts_authorized=1,
        packets_in=100,
        packets_out=20,
        commands_processed=7,
        top_commands={"ping": 4, "weather": 3},
    )
    base.update(overrides)
    return base


class TelemetryCountersTests(unittest.TestCase):
    def test_records_lowercase(self):
        c = telemetry.TelemetryCounters()
        c.record("Ping")
        c.record("PING")
        c.record("weather")
        snap = c.snapshot()
        self.assertEqual(snap["ping"], 2)
        self.assertEqual(snap["weather"], 1)

    def test_top_n_limit(self):
        c = telemetry.TelemetryCounters()
        for i in range(50):
            c.record(f"cmd{i}")
        snap = c.snapshot(top_n=5)
        self.assertEqual(len(snap), 5)

    def test_record_skips_empty(self):
        c = telemetry.TelemetryCounters()
        c.record("")
        c.record(None)  # type: ignore[arg-type]
        self.assertEqual(c.snapshot(), {})

    def test_reset(self):
        c = telemetry.TelemetryCounters()
        c.record("ping")
        c.reset()
        self.assertEqual(c.snapshot(), {})


class AnonIdTests(unittest.TestCase):
    def test_returns_hex_sha256_length(self):
        anon = telemetry.make_anon_id()
        self.assertEqual(len(anon), 64)
        # hex
        int(anon, 16)

    def test_unique_between_calls(self):
        a = telemetry.make_anon_id()
        b = telemetry.make_anon_id()
        self.assertNotEqual(a, b)


class PayloadStructureTests(unittest.TestCase):
    def test_required_fields_present(self):
        p = _sample_payload()
        for key in ("anon_id", "version", "uptime", "modules_count",
                    "commands_count", "watchers_count", "accounts",
                    "packets_in", "packets_out", "commands_processed",
                    "top_commands", "ts"):
            self.assertIn(key, p)
        self.assertIsInstance(p["accounts"], dict)
        self.assertIn("total", p["accounts"])
        self.assertIn("authorized", p["accounts"])

    def test_no_pii_in_clean_payload(self):
        # Чистый payload должен пройти assert_no_pii без исключения.
        telemetry.assert_no_pii(_sample_payload())

    def test_pii_detected_in_dict(self):
        bad = _sample_payload()
        bad["chat_id"] = 12345
        with self.assertRaises(ValueError) as ctx:
            telemetry.assert_no_pii(bad)
        self.assertIn("chat_id", str(ctx.exception))

    def test_pii_detected_in_nested_list(self):
        bad = _sample_payload()
        bad["recent"] = [{"text": "hello", "id": 1}]
        with self.assertRaises(ValueError) as ctx:
            telemetry.assert_no_pii(bad)
        self.assertIn("text", str(ctx.exception))

    def test_pii_detected_in_nested_dict(self):
        bad = _sample_payload()
        bad["meta"] = {"phone": "+79..."}
        with self.assertRaises(ValueError):
            telemetry.assert_no_pii(bad)

    def test_top_commands_is_just_names(self):
        # top_commands должен быть Mapping[str, int]; в нём не должно быть
        # ключей вроде "text" / "chat_id".
        p = _sample_payload(top_commands={"ping": 1, "weather": 2})
        telemetry.assert_no_pii(p)


if __name__ == "__main__":
    unittest.main()
