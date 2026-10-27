import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq.brain import ROOT
from aihq.gitops import sync
from aihq.runtime import Runtime


@unittest.skipUnless(shutil.which("git"), "Git required")
class GitOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.remote = self.directory / "origin.git"
        self.source = self.directory / "source"
        self.checkout = self.directory / "checkout"
        self.source.mkdir()
        for name in ["system", "apps", "themes", "agents", "workflows", "vault", ".agents"]:
            shutil.copytree(ROOT / name, self.source / name)
        shutil.copy2(ROOT / "AGENTS.md", self.source / "AGENTS.md")
        self.git(self.directory, "init", "--bare", str(self.remote))
        self.git(self.source, "init", "-b", "main")
        self.git(self.source, "config", "user.name", "Test")
        self.git(self.source, "config", "user.email", "test@example.invalid")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-m", "Seed")
        self.git(self.source, "remote", "add", "origin", str(self.remote))
        self.git(self.source, "push", "-u", "origin", "main")
        self.git(self.directory, "clone", "-b", "main", str(self.remote), str(self.checkout))
        self.runtime = Runtime(self.checkout, self.directory / "private")
        self.runtime.reconcile()
        self.real_git = self.runtime.brain.git
        self.mock_git = patch.object(self.runtime.brain, "git", side_effect=self.transport)
        self.mock_git.start()
        self.addCleanup(self.mock_git.stop)

    def git(self, directory, *args):
        result = subprocess.run(["git", *args], cwd=directory, capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def transport(self, *args):
        if args == ("remote", "get-url", "origin"):
            return "https://github.com/example/brain.git"
        return self.real_git(*args)

    def update_remote(self, name="Update"):
        with (self.source / "vault" / "Brain.md").open("a") as stream:
            stream.write("\n" + name + "\n")
        self.git(self.source, "add", ".")
        self.git(self.source, "commit", "-m", name)
        self.git(self.source, "push", "origin", "main")
        return self.git(self.source, "rev-parse", "HEAD")

    def test_preview_then_apply_exact_fast_forward_without_hooks(self):
        before = self.git(self.checkout, "rev-parse", "HEAD")
        target = self.update_remote()
        hook = self.checkout / ".git/hooks/post-merge"
        flag = self.directory / "hook-ran"
        hook.write_text("#!/bin/sh\ntouch '" + str(flag) + "'\n")
        hook.chmod(0o755)
        preview = sync(self.runtime)
        self.assertEqual(preview["status"], "preview")
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before)
        result = sync(self.runtime, confirm=True)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), target)
        self.assertFalse(flag.exists())
        self.assertFalse(self.runtime.snapshot()["brain"]["drift"])
        self.assertEqual(sync(self.runtime)["status"], "current")

    def test_confirmation_without_preview_is_rejected(self):
        self.update_remote()
        before = self.git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "Preview"):
            sync(self.runtime, confirm=True)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before)

    def test_changed_remote_invalidates_preview(self):
        self.update_remote()
        sync(self.runtime)
        self.update_remote("Changed again")
        with self.assertRaisesRegex(ValueError, "Preview"):
            sync(self.runtime, confirm=True)

    def test_changing_local_branch_invalidates_preview(self):
        self.update_remote()
        sync(self.runtime)
        self.git(self.checkout, "switch", "-c", "another")
        self.git(self.checkout, "branch", "--set-upstream-to", "origin/main")
        before = self.git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "Preview"):
            sync(self.runtime, confirm=True)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before)

    def test_dirty_checkout_is_preserved(self):
        path = self.checkout / "private-note.txt"
        path.write_text("Keep local")
        with self.assertRaisesRegex(ValueError, "local changes"):
            sync(self.runtime)
        self.assertEqual(path.read_text(), "Keep local")

    def test_divergent_history_is_not_merged(self):
        self.update_remote()
        self.git(self.checkout, "config", "user.name", "Test")
        self.git(self.checkout, "config", "user.email", "test@example.invalid")
        (self.checkout / "local.txt").write_text("local")
        self.git(self.checkout, "add", ".")
        self.git(self.checkout, "commit", "-m", "Local")
        before = self.git(self.checkout, "rev-parse", "HEAD")
        with self.assertRaises(ValueError):
            sync(self.runtime)
        self.assertEqual(self.git(self.checkout, "rev-parse", "HEAD"), before)

    def test_other_transport_is_rejected_before_fetch(self):
        def transport(*args):
            if args == ("remote", "get-url", "origin"):
                return "ext::arbitrary-command"
            return self.real_git(*args)
        with patch.object(self.runtime.brain, "git", side_effect=transport):
            with self.assertRaisesRegex(ValueError, "github.com"):
                sync(self.runtime)


if __name__ == "__main__":
    unittest.main()
