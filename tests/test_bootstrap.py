"""Run the real bootstrap in a disposable Mac/tool simulation, without installs."""
from __future__ import annotations

import shutil
import unittest
from pathlib import Path

import test_fork_push as fork_push


class BootstrapTests(fork_push.GitFixture):
    def setUp(self):
        super().setUp()
        for name in ("bootstrap.sh", "setup.sh", "hq", "scripts/seed_local.py", "AGENTS.md"):
            shutil.copy2(fork_push.SOURCE / name, self.checkout / name)
        for name in ("aihq", "system", "apps", "themes", "agents", "workflows", "vault", ".agents"):
            shutil.copytree(fork_push.SOURCE / name, self.checkout / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        (self.checkout / "tests").mkdir()
        shutil.copy2(fork_push.SOURCE / "tests/test_seed_local.py",
                     self.checkout / "tests/test_seed_local.py")
        self.git("add", "-A")
        self.git("commit", "-m", "Bootstrap fixture")
        self.git("push", str(self.bare), "main")
        self.initial_head = self.local_head()
        self.write_tool("uname", '#!/bin/sh\nprintf "%s\\n" "${TEST_OS:-Darwin}"\n')
        for name in ("brew", "xcode-select", "open", "sudo", "launchctl",
                     "curl", "wget", "spctl", "xattr"):
            self.write_tool(name,
                            '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$HOME/forbidden-calls"\nexit 99\n')

    def run_bootstrap(self, answer="PUSH\n", extra_env=None, arguments=("--yes",),
                      script="bootstrap.sh"):
        result = self.run_script(script, answer, extra_env, arguments)
        self.assertFalse((self.home / "forbidden-calls").exists(),
                         result.stdout + result.stderr)
        return result

    def test_real_bootstrap_without_ai_tools_and_idempotent_retry(self):
        self.assertFalse((self.bin / "codex").exists())
        self.assertFalse((self.bin / "hermes").exists())
        self.assertFalse((self.bin / "python").exists())
        (self.bin / "brew").unlink()
        count = self.commit_count()
        result = self.run_bootstrap()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOCAL BOOTSTRAP COMPLETE", result.stdout)
        self.assertIn('"valid": true', result.stdout)
        self.assertIn("OK", result.stderr)
        database = Path(self.env["AIHQ_HOME"]) / "runtime/os.sqlite3"
        self.assertTrue(database.is_file())
        self.assertFalse(database.is_relative_to(self.checkout))
        self.assertEqual(self.local_head(), self.remote_head())
        self.assertEqual(self.commit_count(), count + 1)
        private_note = Path(self.env["AIHQ_HOME"]) / "knowledge/README.md"
        private_note.write_text("Owner's private notes\n")
        retry = self.run_bootstrap("")
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertIn("Already pushed", retry.stdout)
        self.assertEqual(self.commit_count(), count + 1)
        self.assertEqual(private_note.read_text(), "Owner's private notes\n")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(list(self.home.iterdir()), [])

    def test_bootstrap_rejected_push_can_retry_same_commit(self):
        count = self.commit_count()
        self.reject_next_push()
        failed = self.run_bootstrap()
        self.assertEqual(failed.returncode, 5, failed.stdout + failed.stderr)
        self.assertEqual(self.remote_head(), self.initial_head)
        committed = self.local_head()
        self.assertEqual(self.commit_count(), count + 1)
        retry = self.run_bootstrap()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self.local_head(), committed)
        self.assertEqual(self.remote_head(), committed)
        self.assertEqual(self.commit_count(), count + 1)

    def test_bootstrap_check_does_not_seed_or_contact_github(self):
        for script in ("bootstrap.sh", "setup.sh"):
            with self.subTest(script=script):
                result = self.run_bootstrap("", arguments=("--check",), script=script)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(Path(self.env["AIHQ_HOME"]).exists())
                self.assertFalse((self.root / "gh.log").exists())
                self.assert_untouched()

    def test_setup_uses_the_same_local_bootstrap(self):
        result = self.run_bootstrap(script="setup.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOCAL BOOTSTRAP COMPLETE", result.stdout)
        self.assertEqual(self.local_head(), self.remote_head())

    def test_missing_github_cli_only_postpones_push(self):
        (self.bin / "gh").unlink()
        result = self.run_bootstrap("")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("First push postponed", result.stdout)
        self.assertTrue(Path(self.env["AIHQ_HOME"]).is_dir())
        self.assert_untouched()

    def test_unauthenticated_github_does_not_block_local_setup(self):
        result = self.run_bootstrap("", extra_env={"TEST_AUTH_STATUS": "1"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("First push postponed", result.stdout)
        self.assertNotIn("auth login", (self.root / "gh.log").read_text())
        self.assert_untouched()

    def test_yes_does_not_authorize_push(self):
        result = self.run_bootstrap("")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Canceled", result.stdout)
        self.assert_untouched()

    def test_non_fork_is_rejected_after_local_setup(self):
        result = self.run_bootstrap(extra_env={"TEST_IS_FORK": "false"})
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("LOCAL BOOTSTRAP COMPLETE", result.stdout)
        self.assert_untouched()

    def test_missing_python_fails_without_install_or_seed(self):
        (self.bin / "python3").unlink()
        result = self.run_bootstrap("")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertFalse(Path(self.env["AIHQ_HOME"]).exists())
        self.assert_untouched()

    def assert_rejected_before_seeding(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Seeded ", result.stdout)
        self.assertNotIn("LOCAL BOOTSTRAP COMPLETE", result.stdout)
        self.assertFalse((self.root / "gh.log").exists())
        self.assertFalse((self.root / "transport.log").exists())
        self.assert_untouched()

    def test_private_home_inside_checkout_rejected_before_any_seed_writes(self):
        private_home = self.checkout / ".private-workspace"
        result = self.run_bootstrap(extra_env={"AIHQ_HOME": str(private_home)})
        self.assertIn("outside the repository", result.stderr)
        self.assert_rejected_before_seeding(result)
        self.assertFalse(private_home.exists())
        self.assertFalse(Path(self.env["AIHQ_HOME"]).exists())

    def test_private_home_symlink_rejected_before_any_seed_writes(self):
        private_home = Path(self.env["AIHQ_HOME"])
        destination = self.root / "redirected-workspace"
        destination.mkdir()
        sentinel = destination / "owner-notes.txt"
        sentinel.write_text("Keep the owner's notes\n")
        private_home.symlink_to(destination, target_is_directory=True)
        result = self.run_bootstrap()
        self.assertIn("not a symlink", result.stderr)
        self.assert_rejected_before_seeding(result)
        self.assertTrue(private_home.is_symlink())
        self.assertEqual(private_home.readlink(), destination)
        self.assertEqual(list(destination.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "Keep the owner's notes\n")

    def test_projects_symlink_into_checkout_rejected_before_any_seed_writes(self):
        private_home = Path(self.env["AIHQ_HOME"])
        private_home.mkdir()
        projects = private_home / "projects"
        projects.symlink_to(self.checkout, target_is_directory=True)
        result = self.run_bootstrap()
        self.assertIn("must not be symlinks", result.stderr)
        self.assert_rejected_before_seeding(result)
        self.assertEqual(list(private_home.iterdir()), [projects])
        self.assertTrue(projects.is_symlink())
        self.assertEqual(projects.readlink(), self.checkout)
        self.assertEqual((self.checkout / "README.md").read_text(), "# Example\n")

    def test_validation_failure_blocks_first_push(self):
        (self.checkout / "tests/test_failure.py").write_text(
            "import unittest\nclass Failure(unittest.TestCase):\n"
            "    def test_failure(self):\n        self.fail('fixture verification failed')\n"
        )
        self.git("add", "tests/test_failure.py")
        self.git("commit", "-m", "Failing verification fixture")
        head = self.local_head()
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("fixture verification failed", result.stderr)
        self.assertEqual(self.local_head(), head)
        self.assertEqual(self.remote_head(), self.initial_head)
        self.assertFalse((self.checkout / fork_push.MARKER).exists())
        self.assertFalse((self.root / "gh.log").exists())

    def test_init_failure_blocks_first_push(self):
        (self.checkout / "system/os.json").write_text('{"schema_version": 0}\n')
        self.git("add", "system/os.json")
        self.git("commit", "-m", "Invalid declaration fixture")
        head = self.local_head()
        result = self.run_bootstrap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported system schema", result.stderr)
        self.assertNotIn("LOCAL BOOTSTRAP COMPLETE", result.stdout)
        self.assertEqual(self.local_head(), head)
        self.assertEqual(self.remote_head(), self.initial_head)
        self.assertFalse((self.checkout / fork_push.MARKER).exists())
        self.assertFalse((self.root / "gh.log").exists())

    def test_validate_is_read_only_before_bootstrap(self):
        result = self.run_script("hq", "", arguments=("validate",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(Path(self.env["AIHQ_HOME"]).exists())
        self.assert_untouched()

    def test_default_setup_declines_installs_and_never_configures_ai_tools(self):
        (self.bin / "gh").unlink()
        for name in ("hermes", "codex"):
            self.write_tool(name,
                            '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$HOME/forbidden-calls"\nexit 99\n')
        result = self.run_bootstrap("NO\n", arguments=())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Install gh using Homebrew", result.stdout)
        self.assertIn("First push postponed", result.stdout)
        self.assert_untouched()

    def test_invalid_arguments_and_unsupported_os_make_no_changes(self):
        for arguments, extra_env in (
            (("--unknown",), {}),
            (("--check", "--unknown"), {}),
            (("--yes",), {"TEST_OS": "Linux"}),
        ):
            with self.subTest(arguments=arguments, extra_env=extra_env):
                result = self.run_bootstrap("", arguments=arguments, extra_env=extra_env)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertFalse(Path(self.env["AIHQ_HOME"]).exists())
                self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
