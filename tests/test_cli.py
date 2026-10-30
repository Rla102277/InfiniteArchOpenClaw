import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from aihq.brain import ROOT


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "private"
        self.env = {**os.environ, "AIHQ_HOME": str(self.home), "PYTHONDONTWRITEBYTECODE": "1", "AIHQ_GIT_BRAIN": "off"}

    def run_cli(self, *args, code=0):
        result = subprocess.run([sys.executable, "-m", "aihq.cli", *args], cwd=ROOT, env=self.env,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_validate_and_hermes_plan_do_not_create_private_workspace(self):
        self.assertTrue(json.loads(self.run_cli("validate").stdout)["valid"])
        self.assertIn("127.0.0.1:11434", self.run_cli("hermes-plan").stdout)
        self.assertFalse(self.home.exists())

    def test_init_submit_execute_restart_and_backup(self):
        self.run_cli("init")
        self.run_cli("action", json.dumps({"action": "task.create", "title": "Remember",
            "operation": "memory.put", "payload": {"key": "local", "content": "Private test memory"}}))
        processed = json.loads(self.run_cli("worker", "--once").stdout)
        self.assertEqual(len(processed["processed"]), 1)
        state = json.loads(self.run_cli("state").stdout)
        self.assertEqual(state["tasks"][0]["status"], "succeeded")
        self.assertEqual(state["memories"][0]["content"], "Private test memory")
        self.assertFalse(json.loads(self.run_cli("backup").stdout)["written"])
        self.assertEqual(list((self.home / "exports").iterdir()), [])
        backup = json.loads(self.run_cli("backup", "--confirm").stdout)
        connection = sqlite3.connect(backup["destination"])
        try:
            self.assertEqual(connection.execute("SELECT content FROM memories").fetchone()[0], "Private test memory")
        finally:
            connection.close()
        packet = json.loads(self.run_cli("handoff").stdout)
        self.assertNotIn("Private test memory", json.dumps(packet))

    def test_invalid_actions_return_errors_without_tracebacks(self):
        self.run_cli("init")
        for payload in ["not-json", "[]", '{"action":"task.create","operation":[]}', '{"action":"preferences.set","panels":[{}]}']:
            with self.subTest(payload=payload):
                response = self.run_cli("action", payload, code=2)
                self.assertNotIn("Traceback", response.stderr)
        self.run_cli("worker", "--once", "--interval", "0", code=2)
        self.run_cli("reconcile", "--confirm", code=2)


if __name__ == "__main__":
    unittest.main()
