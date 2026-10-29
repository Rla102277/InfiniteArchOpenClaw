"""First-push contract test with real Git and a local bare remote.

The push is allowed only to the owner's own private, non-fork repository made from the template.

Never calls the network; fake gh only validates branch behavior.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "first-push.sh"


@unittest.skipUnless(shutil.which("git"), "git is required")
class FirstPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bare = self.root / "remote.git"
        self.checkout = self.root / "checkout"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.checkout.mkdir()
        self.git_path = shutil.which("git")
        subprocess.run(["git", "init", "--bare", str(self.bare)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.checkout, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Friend"], cwd=self.checkout, check=True)
        subprocess.run(["git", "config", "user.email", "friend@example.invalid"], cwd=self.checkout, check=True)
        (self.checkout / "README.md").write_text("# Example\n")
        (self.checkout / "scripts").mkdir()
        shutil.copy2(SOURCE, self.checkout / "scripts/first-push.sh")
        subprocess.run(["git", "add", "-A"], cwd=self.checkout, check=True)
        subprocess.run(["git", "commit", "-m", "Initial"], cwd=self.checkout, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", str(self.bare)], cwd=self.checkout, check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=self.checkout, check=True, capture_output=True)
        (self.bin / "git").write_text(
            '#!/bin/sh\n'
            'if [ "$1" = remote ] && [ "$2" = get-url ]; then\n'
            ' echo "${TEST_ORIGIN:-https://github.com/friend/my-assistant.git}"; exit 0\n'
            'fi\n'
            f'exec "{self.git_path}" "$@"\n')
        (self.bin / "gh").write_text(
            '#!/bin/sh\n'
            '[ "$1" = auth ] && exit 0\n'
            'if [ "$1" = api ]; then\n'
            ' case "$*" in *".login"*) echo "${TEST_LOGIN:-friend}";; *".id"*) echo 999;; esac\n'
            ' exit 0\n'
            'fi\n'
            'if [ "$1" = repo ]; then\n'
            ' [ -n "$TEST_GH_DOWN" ] && exit 1\n'
            ' echo "${TEST_REPO_STATE:-PRIVATE false}"; exit 0\n'
            'fi\n'
            'exit 2\n')
        for cmd in self.bin.iterdir():
            cmd.chmod(0o755)
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}

    def run_first_push(self, answer="PUSH", extra_env=None):
        return subprocess.run(["bash", "scripts/first-push.sh"], cwd=self.checkout,
                              input=answer + "\n", text=True, capture_output=True,
                              env={**self.env, **(extra_env or {})})

    def local_head(self):
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.checkout, text=True).strip()

    def remote_head(self):
        return subprocess.check_output(["git", "--git-dir", str(self.bare), "rev-parse", "refs/heads/main"], text=True).strip()

    def test_first_push_and_retry_noop(self):
        result = self.run_first_push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.local_head(), self.remote_head())
        retry = self.run_first_push("")
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertIn("Already pushed", retry.stdout)
        self.assertEqual(self.local_head(), self.remote_head())

    def test_cancel_does_not_commit(self):
        head = self.local_head()
        result = self.run_first_push("NO")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.local_head(), head)
        self.assertFalse((self.checkout / "onboarding/FIRST_BOOTSTRAP.md").exists())

    def assert_rejected(self, extra_env, message):
        head = self.remote_head()
        result = self.run_first_push(extra_env=extra_env)
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertEqual(self.remote_head(), head)
        self.assertFalse((self.checkout / "onboarding/FIRST_BOOTSTRAP.md").exists())

    def test_public_repository_rejected(self):
        self.assert_rejected({"TEST_REPO_STATE": "PUBLIC false"}, "is not private")

    def test_fork_rejected(self):
        self.assert_rejected({"TEST_REPO_STATE": "PRIVATE true"}, "is a fork")

    def test_template_itself_rejected(self):
        self.assert_rejected({"TEST_ORIGIN": "https://github.com/Rla102277/InfiniteArchOpenClaw.git",
                              "TEST_LOGIN": "Rla102277"}, "the template itself")

    def test_unconfirmed_visibility_rejected(self):
        self.assert_rejected({"TEST_GH_DOWN": "1"}, "could not confirm")

    def test_failed_network_push_retries_without_duplicate(self):
        script = self.bin / "git"
        old = script.read_text()
        flag = self.root / "fail_once"
        script.write_text(old.replace(
            f'exec "{self.git_path}" "$@"',
            'if [ "$1" = push ] && [ -f "' + str(flag) + '" ]; then\n'
            ' rm "' + str(flag) + '"; exit 1\n'
            'fi\n'
            f'exec "{self.git_path}" "$@"'))
        flag.touch()
        failed = self.run_first_push()
        self.assertEqual(failed.returncode, 5, failed.stdout + failed.stderr)
        committed = self.local_head()
        retried = self.run_first_push()
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(self.local_head(), committed)
        self.assertEqual(self.remote_head(), committed)


if __name__ == "__main__":
    unittest.main()
