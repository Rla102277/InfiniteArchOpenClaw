import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq import adapters, agentops
from aihq.runtime import PANELS, Runtime

if __package__:
    from .test_brain import brain_fixture
else:
    from test_brain import brain_fixture

GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


class AgentOpsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, env=GIT_ENV)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=self.repo, check=True, env=GIT_ENV)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()

    def test_agent_proposal_is_a_file_until_accepted(self):
        result = self.runtime.action({"action": "agent.propose", "id": "print-checker", "role": "Checks print jobs",
                                      "adapter": "claude", "skills": ["ia-log"]})
        self.assertTrue((self.repo / "proposals/agents/print-checker.json").is_file())
        self.assertNotIn("print-checker", [a["id"] for a in self.runtime.snapshot()["agents"]])
        self.assertIn("Accept", result["message"])
        self.runtime.action({"action": "agent.accept", "id": "print-checker"})
        snapshot = self.runtime.snapshot()
        agent = next(a for a in snapshot["agents"] if a["id"] == "print-checker")
        self.assertTrue(agent["edits"])
        self.assertFalse(snapshot["brain"]["drift"])
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "agent.propose", "id": "x", "role": "r", "adapter": "skynet"})

    def test_add_skill_writes_skill_md_and_reconciles(self):
        self.runtime.action({"action": "skill.create", "name": "show-packet", "description": "Use when building a show entry",
                             "model": "haiku", "home": "os", "body": "1. Read shows/.\n2. Write the packet."})
        text = (self.repo / ".agents/skills/show-packet/SKILL.md").read_text()
        self.assertIn("model: haiku", text)
        self.assertIn("Write the packet.", text)
        self.assertFalse(self.runtime.snapshot()["brain"]["drift"])
        with self.assertRaises(FileExistsError):
            self.runtime.action({"action": "skill.create", "name": "show-packet", "description": "d", "body": "b"})
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "skill.create", "name": "other", "description": "d", "body": "b", "model": "gpt"})

    def test_edit_mode_is_only_for_claude_and_codex_and_binds_to_the_approval(self):
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "task.create", "title": "t", "operation": "agent.prompt",
                                 "payload": {"adapter": "hermes", "prompt": "change things", "mode": "edit"}})
        self.runtime.action({"action": "task.create", "title": "t", "operation": "agent.prompt",
                             "payload": {"adapter": "claude", "prompt": "fix the typo", "mode": "edit", "agent": "claude"}})
        task = self.runtime.snapshot()["tasks"][0]
        self.assertEqual(task["payload"]["mode"], "edit")
        self.assertEqual(task["status"], "awaiting_approval")
        self.assertEqual(adapters.command("claude", "p", "edit")[:5],
                         ["claude", "-p", "--permission-mode", "acceptEdits", "--allowedTools"])
        self.assertNotIn("Bash", adapters.command("claude", "p", "edit")[5])

    def test_edit_runs_in_a_worktree_and_commits_on_an_agent_branch(self):
        fake = Path(self.temporary.name) / "bin" / "claude"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\necho 'agent wrote this' > AGENT_NOTE.md\necho done\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        with patch.dict(os.environ, {"PATH": f"{fake.parent}:{os.environ['PATH']}", **{k: v for k, v in GIT_ENV.items() if k.startswith("GIT_")}}):
            self.runtime.action({"action": "task.create", "title": "Write a note", "operation": "agent.prompt",
                                 "payload": {"adapter": "claude", "prompt": "write a note", "mode": "edit"}})
            approval = self.runtime.snapshot()["approvals"][0]
            self.runtime.action({"action": "approval.decide", "id": approval["id"], "decision": "approved"})
            self.runtime.tick(allow_external=True)
        task = self.runtime.snapshot()["tasks"][0]
        self.assertEqual(task["status"], "succeeded", task["error"])
        self.assertIn("agent/write-a-note-", task["result"])
        self.assertFalse((self.repo / "AGENT_NOTE.md").exists())
        branches = subprocess.run(["git", "branch", "--list", "agent/*"], cwd=self.repo, capture_output=True, text=True).stdout
        self.assertIn("agent/write-a-note-", branches)

    def test_schedule_run_now_and_history(self):
        schedule = self.runtime.action({"action": "schedule.create", "name": "Check", "workflow": "agent-check", "interval_seconds": 86400})
        self.runtime.action({"action": "schedule.run", "id": schedule["id"]})
        self.runtime.tick()
        snapshot = self.runtime.snapshot()
        entry = next(s for s in snapshot["schedules"] if s["id"] == schedule["id"])
        self.assertEqual(entry["history"][0]["status"], "succeeded")
        self.assertIn("agent-availability", [m["key"] for m in snapshot["memories"]])

    def test_new_panels_show_for_an_uncustomized_workspace(self):
        panels = self.runtime.snapshot()["preferences"]["panels"]
        for panel in PANELS:
            self.assertIn(panel, panels)

    def test_git_status_reports_branch_and_agent_branches(self):
        agentops._git_cache.clear()
        status = agentops.git_status(self.repo)
        self.assertTrue(status["available"])
        self.assertEqual(status["agent_branches"], [])


if __name__ == "__main__":
    unittest.main()
