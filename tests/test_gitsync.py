import subprocess
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from aihq import gitsync


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


class GitSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.domain = self.tmp / "domain"
        git(self.tmp, "clone", "-q", str(self.remote), str(self.domain))
        git(self.domain, "config", "user.email", "t@example.com")
        git(self.domain, "config", "user.name", "T")
        (self.domain / "governance").mkdir()
        (self.domain / "governance" / "CANON.md").write_text("canon\n")
        (self.domain / "hq").mkdir()
        (self.domain / "hq" / "HQ.md").write_text("hq\n")
        git(self.domain, "add", "-A")
        git(self.domain, "commit", "-q", "-m", "init")
        git(self.domain, "push", "-q", "-u", "origin", "main")

    def test_nothing_to_do(self):
        out = gitsync.sync(self.domain)
        self.assertEqual((out["changed"], out["committed"], out["pushed"]), (0, False, False))

    def test_commits_only_hq_and_pushes(self):
        (self.domain / "hq" / "log.md").write_text("- 10:00 · os · did a thing\n")
        (self.domain / "governance" / "CANON.md").write_text("edited by hand, not for the OS to commit\n")
        out = gitsync.sync(self.domain)
        self.assertTrue(out["committed"] and out["pushed"], out)
        log = git(self.domain, "log", "--oneline", "origin/main").stdout
        self.assertIn("brain sync", log)
        status = git(self.domain, "status", "--porcelain").stdout
        self.assertIn("governance/CANON.md", status, "the hand edit is still uncommitted, untouched")
        shown = git(self.domain, "show", "--stat", "--oneline", "HEAD").stdout
        self.assertNotIn("governance", shown)

    def test_rebases_onto_remote_changes(self):
        other = self.tmp / "other"
        git(self.tmp, "clone", "-q", str(self.remote), str(other))
        git(other, "config", "user.email", "o@example.com")
        git(other, "config", "user.name", "O")
        (other / "TODO.md").write_text("- [ ] thing\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "todo from the other mac")
        git(other, "push", "-q")
        (self.domain / "hq" / "log.md").write_text("line\n")
        out = gitsync.sync(self.domain)
        self.assertTrue(out["pushed"], out)
        self.assertTrue((self.domain / "TODO.md").exists(), "remote work arrived")
        log = git(self.domain, "log", "--oneline").stdout.splitlines()
        self.assertEqual(len(log), 3)


if __name__ == "__main__":
    unittest.main()


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class GitSyncFailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.domain = self.tmp / "domain"
        git(self.tmp, "init", "-q", "-b", "main", str(self.domain))
        git(self.domain, "config", "user.email", "t@example.com")
        git(self.domain, "config", "user.name", "T")
        (self.domain / "hq").mkdir()
        git(self.domain, "add", "-A")
        git(self.domain, "commit", "-q", "--allow-empty", "-m", "init")
        self.real_run = gitsync.subprocess.run
        self.calls = []
        self._sleep = mock.patch.object(gitsync.time, "sleep").start()

    def tearDown(self):
        mock.patch.stopall()
        gitsync.subprocess.run = self.real_run

    def _stub(self, failures):
        """Patch subprocess.run; failures maps a command substring to a queue of fake results."""
        def fake(cmd, **kwargs):
            self.calls.append(tuple(cmd))
            for key, queue in failures.items():
                if key in cmd and queue:
                    return queue.pop(0)
            return self.real_run(cmd, **kwargs)
        gitsync.subprocess.run = fake

    def _count(self, key):
        return sum(1 for c in self.calls if key in c)

    def test_status_failure_reports_error(self):
        self._stub({"status": [_FakeProc(128, "", "fatal: something broke")]})
        out = gitsync.sync(self.domain, push=False)
        self.assertIn("error", out)
        self.assertFalse(out["committed"])
        self.assertFalse(out["pushed"])

    def test_rev_list_failure_reports_error(self):
        (self.domain / "hq" / "log.md").write_text("line\n")
        self._stub({"rev-list": [_FakeProc(128, "", "fatal: no upstream info")]})
        out = gitsync.sync(self.domain)
        self.assertIn("error", out)
        self.assertFalse(out["pushed"])

    def test_edeadlk_retries_then_succeeds(self):
        self._stub({"status": [_FakeProc(128, "", "fatal: Resource deadlock avoided")]})
        (self.domain / "hq" / "log.md").write_text("line\n")
        out = gitsync.sync(self.domain, push=False)
        self.assertTrue(out["committed"], out)
        self.assertEqual(self._count("status"), 2)

    def test_non_transient_failure_not_retried(self):
        self._stub({"status": [_FakeProc(128, "", "fatal: not a git repository"),
                               _FakeProc(0, "?? hq/x.md\n", "")]})
        out = gitsync.sync(self.domain, push=False)
        self.assertIn("error", out)
        self.assertEqual(self._count("status"), 1)


if __name__ == "__main__":
    unittest.main()
