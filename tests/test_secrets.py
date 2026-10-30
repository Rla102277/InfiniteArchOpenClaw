import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aihq import secrets


class SecretsTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"AIHQ_HOME": self.home, "CURATOR_SECRETS_BACKEND": "file"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_put_get_present_delete_file_backend(self):
        self.assertFalse(secrets.present("openai"))
        secrets.put("openai", "sk-test-123")
        self.assertEqual(secrets.get("openai"), "sk-test-123")
        self.assertTrue(secrets.present("openai"))
        path = Path(self.home) / "secrets" / "openai"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        secrets.delete("openai")
        self.assertFalse(secrets.present("openai"))

    def test_env_wins_and_resolve(self):
        secrets.put("telegram-bot-token", "from-file")
        with mock.patch.dict(os.environ, {"CURATOR_TELEGRAM_BOT_TOKEN": "from-env"}):
            self.assertEqual(secrets.get("telegram-bot-token"), "from-env")
            self.assertEqual(secrets.resolve("secret:telegram-bot-token"), "from-env")
        self.assertEqual(secrets.resolve("plain"), "plain")
        self.assertEqual(secrets.resolve("secret:missing"), "")

    def test_bad_names_and_empty_values_refused(self):
        for bad in ("", "../x", "-lead", "a b"):
            with self.assertRaises(ValueError):
                secrets.put(bad, "v")
        with self.assertRaises(ValueError):
            secrets.put("ok", "   ")

    def test_keychain_backend_calls_security_without_echoing(self):
        calls = []

        class Done:
            returncode, stdout, stderr = 0, "kc-value\n", ""

        def fake_run(args, **kwargs):
            calls.append(args)
            return Done()

        with mock.patch.dict(os.environ, {"CURATOR_SECRETS_BACKEND": "keychain"}), mock.patch("subprocess.run", fake_run), mock.patch("aihq.core.local_config", return_value={}):
            secrets.put("gemini", "g-key")
            self.assertEqual(secrets.get("gemini"), "kc-value")
        self.assertEqual(calls[0][:7], ["security", "add-generic-password", "-U", "-s", "agentic-os", "-a", "gemini"])
        self.assertEqual(calls[1][:6], ["security", "find-generic-password", "-s", "agentic-os", "-a", "gemini"])


if __name__ == "__main__":
    unittest.main()
