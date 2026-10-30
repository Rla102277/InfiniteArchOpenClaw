"""Offline fork/push tests: real Git repositories, fake GitHub and transport."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
MARKER = "onboarding/FIRST_BOOTSTRAP.md"


@unittest.skipUnless(shutil.which("git"), "git is required")
class GitFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bare = self.root / "fork.git"
        self.checkout = self.root / "checkout"
        self.bin = self.root / "bin"
        self.home = self.root / "home"
        for directory in (self.checkout, self.bin, self.home):
            directory.mkdir()
        self.git_path = shutil.which("git")
        self.bash_path = shutil.which("bash")
        self.env = {
            name: value for name, value in os.environ.items()
            if not name.startswith(("GIT_", "AIHQ_", "TEST_", "GH_"))
        }
        self.env.update({
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TERMINAL_PROMPT": "0",
            "AIHQ_HOME": str(self.root / "private-workspace"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PATH": str(self.bin),
            "TEST_REAL_GIT": self.git_path,
            "TEST_BARE": str(self.bare),
            "TEST_TRANSPORT_LOG": str(self.root / "transport.log"),
            "TEST_GH_LOG": str(self.root / "gh.log"),
        })
        for command in ("bash", "dirname", "sed", "tr", "awk", "mkdir", "cat"):
            (self.bin / command).symlink_to(shutil.which(command))
        (self.bin / "python3").symlink_to(sys.executable)
        self.write_tool("gh", """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_GH_LOG"
if [ "$1" = auth ]; then
 [ "$2" = status ] || exit 90
 exit "${TEST_AUTH_STATUS:-0}"
fi
if [ "$1" = api ]; then
 case "$*" in
  *".login"*) echo "${TEST_LOGIN:-friend}";;
  *".id"*) echo 999;;
  *) exit 90;;
 esac
 exit 0
fi
if [ "$1" = repo ]; then
 [ "${TEST_REPO_FAILURE:-0}" = 0 ] || exit 1
 echo "${TEST_IS_FORK:-true}"
 exit 0
fi
exit 90
""")
        self.write_tool("git", f"""#!{sys.executable}
import os
import sys
arguments = sys.argv[1:]
command_index = 0
while arguments[command_index] == "-c":
    command_index += 2
command = arguments[command_index]
if command in ("ls-remote", "push"):
    with open(os.environ["TEST_TRANSPORT_LOG"], "a") as stream:
        stream.write(command + "\\n")
    if command == "ls-remote" and os.environ.get("TEST_LS_REMOTE_FAIL") == "1":
        sys.exit(1)
    if "origin" not in arguments[command_index + 1:]:
        sys.exit("Unexpected network target")
    arguments[arguments.index("origin", command_index + 1)] = os.environ["TEST_BARE"]
os.execv(os.environ["TEST_REAL_GIT"], [os.environ["TEST_REAL_GIT"], *arguments])
""")
        self.git("init", "--bare", str(self.bare), cwd=self.root)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Friend")
        self.git("config", "user.email", "friend@example.invalid")
        (self.checkout / "README.md").write_text("# Example\n")
        (self.checkout / "scripts").mkdir()
        shutil.copy2(SOURCE / "scripts/first-push.sh", self.checkout / "scripts/first-push.sh")
        self.git("add", "-A")
        self.git("commit", "-m", "Initial")
        self.git("remote", "add", "origin", "https://github.com/friend/InfiniteArchOpenClaw.git")
        self.git("push", str(self.bare), "main")
        self.initial_head = self.local_head()

    def write_tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def git(self, *arguments, cwd=None):
        return subprocess.check_output(
            [self.git_path, *arguments], cwd=cwd or self.checkout,
            text=True, stderr=subprocess.STDOUT, env=self.env,
        ).strip()

    def run_script(self, script, answer="PUSH\n", extra_env=None, arguments=()):
        return subprocess.run(
            [self.bash_path, script, *arguments], cwd=self.checkout,
            input=answer, text=True, capture_output=True, timeout=30,
            env={**self.env, **(extra_env or {})},
        )

    def run_first_push(self, answer="PUSH\n", extra_env=None):
        return self.run_script("scripts/first-push.sh", answer, extra_env)

    def local_head(self):
        return self.git("rev-parse", "HEAD")

    def remote_head(self):
        return self.git("--git-dir", str(self.bare), "rev-parse", "refs/heads/main")

    def commit_count(self):
        return int(self.git("rev-list", "--count", "HEAD"))

    def reject_next_push(self):
        flag = self.root / "reject-next-push"
        flag.touch()
        hook = self.bare / "hooks/pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            f"if [ -f {shlex.quote(str(flag))} ]; then\n"
            f" /bin/rm {shlex.quote(str(flag))}\n"
            " echo 'Simulated remote rejection' >&2\n exit 1\nfi\n"
        )
        hook.chmod(0o755)

    def assert_untouched(self):
        self.assertEqual(self.local_head(), self.initial_head)
        self.assertEqual(self.remote_head(), self.initial_head)
        self.assertFalse((self.checkout / MARKER).exists())
        self.assertEqual(self.git("status", "--porcelain"), "")


class ForkPushTests(GitFixture):
    def test_first_push_and_retry_noop(self):
        count = self.commit_count()
        result = self.run_first_push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        committed = self.local_head()
        self.assertEqual(committed, self.remote_head())
        self.assertEqual(self.commit_count(), count + 1)
        self.assertEqual(self.git("status", "--porcelain"), "")
        retry = self.run_first_push("")
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertIn("Already pushed", retry.stdout)
        self.assertEqual(self.local_head(), committed)
        self.assertEqual(self.commit_count(), count + 1)

    def test_cancel_and_eof_do_not_write(self):
        for answer in ("NO\n", ""):
            with self.subTest(answer=answer):
                result = self.run_first_push(answer)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assert_untouched()
                self.assertFalse((self.checkout / "onboarding").exists())

    def test_unreachable_fork_leaves_clean_checkout_and_can_retry(self):
        failed = self.run_first_push(extra_env={"TEST_LS_REMOTE_FAIL": "1"})
        self.assertEqual(failed.returncode, 4, failed.stdout + failed.stderr)
        self.assert_untouched()
        retried = self.run_first_push()
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(self.local_head(), self.remote_head())

    def test_rejected_push_retries_saved_commit_without_duplicate(self):
        count = self.commit_count()
        self.reject_next_push()
        failed = self.run_first_push()
        self.assertEqual(failed.returncode, 5, failed.stdout + failed.stderr)
        self.assertIn("Simulated remote rejection", failed.stderr)
        committed = self.local_head()
        self.assertNotEqual(committed, self.initial_head)
        self.assertEqual(self.remote_head(), self.initial_head)
        self.assertEqual(self.commit_count(), count + 1)
        self.assertEqual(self.git("status", "--porcelain"), "")
        retried = self.run_first_push()
        self.assertEqual(retried.returncode, 0, retried.stdout + retried.stderr)
        self.assertEqual(self.local_head(), committed)
        self.assertEqual(self.remote_head(), committed)
        self.assertEqual(self.commit_count(), count + 1)
        noop = self.run_first_push("")
        self.assertEqual(noop.returncode, 0, noop.stdout + noop.stderr)
        self.assertEqual(self.commit_count(), count + 1)

    def test_account_fork_and_auth_guards(self):
        for extra_env in (
            {"TEST_LOGIN": "upstream"},
            {"TEST_IS_FORK": "false"},
            {"TEST_REPO_FAILURE": "1"},
            {"TEST_AUTH_STATUS": "1"},
        ):
            with self.subTest(extra_env=extra_env):
                result = self.run_first_push(extra_env=extra_env)
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()
                self.assertFalse((self.root / "transport.log").exists())

    def test_unknown_or_multiple_remote_urls_rejected(self):
        origin = self.git("remote", "get-url", "origin")
        for value in (
            "https://example.invalid/friend/repo.git",
            "https://github.com/friend/repo.git/extra",
            "https://github.com/upstream/repo.git",
        ):
            with self.subTest(origin=value):
                self.git("remote", "set-url", "origin", value)
                result = self.run_first_push()
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()
        self.git("remote", "set-url", "origin", origin)
        self.git("config", "--add", "remote.origin.url", "https://github.com/friend/other.git")
        result = self.run_first_push()
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assert_untouched()

    def test_push_url_cannot_redirect_or_fan_out(self):
        for values in (
            ["https://github.com/upstream/repo.git"],
            ["https://example.invalid/repo.git"],
            [self.git("remote", "get-url", "origin"), "https://github.com/friend/other.git"],
        ):
            with self.subTest(push_urls=values):
                configured = self.git("config", "--get-regexp", "^remote\\.origin\\.")
                if "pushurl" in configured:
                    self.git("config", "--unset-all", "remote.origin.pushurl")
                for value in values:
                    self.git("config", "--add", "remote.origin.pushurl", value)
                result = self.run_first_push()
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assert_untouched()
                self.assertFalse((self.root / "transport.log").exists())

    def test_dirty_tree_and_detached_head_are_rejected(self):
        for path in ("README.md", "private.txt"):
            with self.subTest(path=path):
                target = self.checkout / path
                previous = target.read_text() if target.exists() else None
                target.write_text("Owner edits\n")
                result = self.run_first_push()
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertEqual(self.local_head(), self.initial_head)
                self.assertEqual(target.read_text(), "Owner edits\n")
                if previous is None:
                    target.unlink()
                else:
                    target.write_text(previous)
        self.git("checkout", "--detach")
        result = self.run_first_push()
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assert_untouched()

    def test_missing_origin_is_not_initialized_or_pushed(self):
        self.git("remote", "remove", "origin")
        result = self.run_first_push()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assert_untouched()

    def test_ssh_and_case_insensitive_owner(self):
        for origin in (
            "git@github.com:Friend/InfiniteArchOpenClaw.git",
            "ssh://git@github.com/Friend/InfiniteArchOpenClaw.git",
        ):
            with self.subTest(origin=origin):
                self.git("remote", "set-url", "origin", origin)
                result = self.run_first_push()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.local_head(), self.remote_head())

    def test_follow_tags_setting_does_not_push_tags(self):
        self.git("tag", "-a", "private-tag", "-m", "Local only")
        self.git("config", "push.followTags", "true")
        result = self.run_first_push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("--git-dir", str(self.bare), "tag"), "")

    def test_identity_is_local_and_uses_noreply_address(self):
        self.git("config", "--unset", "user.name")
        self.git("config", "--unset", "user.email")
        result = self.run_first_push()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git("config", "--local", "user.email"),
                         "999+friend@users.noreply.github.com")
        self.assertEqual(list(self.home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
