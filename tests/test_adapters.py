import hashlib
import http.client
import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aihq import adapters


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prompt = "--untrusted-option ; $(touch sentinel)\nReview this text only."
        self.popen_patch = patch("aihq.adapters.subprocess.Popen")
        self.popen = self.popen_patch.start()
        self.addCleanup(self.popen_patch.stop)
        self.http_patch = patch("aihq.adapters.http.client.HTTPConnection")
        self.http = self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def process_output(self, output=b"Mock CLI result", returncode=0):
        process = Mock(pid=99999999)
        process.wait.return_value = returncode

        def spawn(args, **kwargs):
            kwargs["stdout"].write(output)
            return process

        self.popen.side_effect = spawn
        return process

    def approved_cli(self):
        executable = self.root / "approved-codex"
        executable.write_bytes(b"Approved executable; never actually launched")
        brain = Mock()
        brain.fingerprint.return_value = "approved-brain"
        brain.revision.return_value = {"head": "approved-revision", "dirty": False}
        payload = {"adapter": "codex", "prompt": self.prompt, "model": "local-model"}
        return executable, brain, payload

    def test_context_records_exact_executable_bytes_payload_brain_and_revision(self):
        executable, brain, payload = self.approved_cli()
        with patch("aihq.adapters.shutil.which", return_value=str(executable)) as which:
            context = adapters.context(payload, brain)
        self.assertEqual(context, {
            "payload": payload, "brain": "approved-brain", "head": "approved-revision",
            "executable": str(executable), "binary": hashlib.sha256(executable.read_bytes()).hexdigest(),
        })
        which.assert_called_once_with("codex")
        self.popen.assert_not_called()

    def test_approved_execute_launches_verified_absolute_path_without_second_lookup(self):
        executable, brain, payload = self.approved_cli()
        alternative = self.root / "unapproved-codex"
        alternative.write_bytes(b"Unapproved PATH replacement")
        self.process_output()
        with patch("aihq.adapters.shutil.which", return_value=str(executable)) as which:
            expected = adapters.binding(payload, brain)
            which.reset_mock()
            which.side_effect = [str(executable), str(alternative)]
            result = adapters.execute(payload, self.root, expected_binding=expected, brain=brain)
        self.assertEqual(result, "Mock CLI result")
        which.assert_called_once_with("codex")
        self.assertEqual(self.popen.call_args.args[0][0], str(executable))
        self.assertTrue(Path(self.popen.call_args.args[0][0]).is_absolute())
        self.http.assert_not_called()

    def test_approved_execute_requires_brain_without_launch_or_network(self):
        with self.assertRaisesRegex(ValueError, "brain context"):
            adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root, expected_binding="unverifiable")
        self.popen.assert_not_called()
        self.http.assert_not_called()

    def test_execute_rechecks_changed_approval_context_before_launch(self):
        for changed in ("prompt", "brain", "revision", "binary", "executable"):
            executable, brain, payload = self.approved_cli()
            with self.subTest(changed=changed), patch("aihq.adapters.shutil.which", return_value=str(executable)) as which:
                expected = adapters.binding(payload, brain)
                if changed == "prompt":
                    payload = {**payload, "prompt": "An unapproved request"}
                elif changed == "brain":
                    brain.fingerprint.return_value = "unapproved-brain"
                elif changed == "revision":
                    brain.revision.return_value = {"head": "unapproved-revision"}
                elif changed == "binary":
                    executable.write_bytes(b"Unapproved replacement")
                else:
                    alternate = self.root / "alternate-codex"
                    alternate.write_bytes(executable.read_bytes())
                    which.return_value = str(alternate)
                with self.assertRaisesRegex(ValueError, "context changed"):
                    adapters.execute(payload, self.root, expected_binding=expected, brain=brain)
        self.popen.assert_not_called()
        self.http.assert_not_called()

    def test_executable_rehash_blocks_swap_after_context_validation(self):
        executable, brain, payload = self.approved_cli()
        original_command = adapters.command

        def replace_during_argument_preparation(adapter, prompt):
            executable.write_bytes(b"Changed after context was validated")
            return original_command(adapter, prompt)

        with patch("aihq.adapters.shutil.which", return_value=str(executable)) as which:
            expected = adapters.binding(payload, brain)
            which.reset_mock()
            with patch("aihq.adapters.command", side_effect=replace_during_argument_preparation):
                with self.assertRaisesRegex(ValueError, "executable changed"):
                    adapters.execute(payload, self.root, expected_binding=expected, brain=brain)
            which.assert_called_once_with("codex")
        self.popen.assert_not_called()
        self.http.assert_not_called()

    def test_stale_ollama_approval_is_rejected_before_opening_connection(self):
        _, brain, _ = self.approved_cli()
        payload = {"adapter": "ollama", "prompt": "Review", "model": "existing-model"}
        expected = adapters.binding(payload, brain)
        brain.fingerprint.return_value = "unapproved-brain"
        with self.assertRaisesRegex(ValueError, "context changed"):
            adapters.execute(payload, self.root, expected_binding=expected, brain=brain)
        self.http.assert_not_called()
        self.popen.assert_not_called()

    def test_command_lists_preserve_prompt_as_one_argument(self):
        expected = {
            "hermes": ["hermes", "chat", "--oneshot", "--max-turns", "8", "-q", self.prompt],
            "codex": ["codex", "exec", "--sandbox", "read-only", "--color", "never", "--", self.prompt],
            "claude": ["claude", "-p", "--permission-mode", "plan", "--tools", "", "--", self.prompt],
        }
        for adapter, args in expected.items():
            with self.subTest(adapter=adapter):
                self.assertEqual(adapters.command(adapter, self.prompt), args)
        for adapter in ("devin", "ollama", "unknown"):
            with self.subTest(adapter=adapter), self.assertRaises(ValueError):
                adapters.command(adapter, self.prompt)

    def test_cli_uses_resolved_binary_isolated_process_and_no_shell(self):
        for adapter in ("hermes", "codex", "claude"):
            process = self.process_output()
            executable = str(self.root / adapter)
            with self.subTest(adapter=adapter), patch("aihq.adapters.shutil.which", return_value=executable):
                result = adapters.execute({"adapter": adapter, "prompt": self.prompt}, self.root, timeout=7)
                self.assertEqual(result, "Mock CLI result")
                args, kwargs = self.popen.call_args
                self.assertEqual(args[0], [executable, *adapters.command(adapter, self.prompt)[1:]])
                self.assertEqual(kwargs["cwd"], self.root)
                self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
                self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
                self.assertTrue(kwargs["start_new_session"])
                self.assertFalse(kwargs.get("shell", False))
                process.wait.assert_called_once_with(timeout=7)
        self.assertFalse((self.root / "sentinel").exists())
        self.http.assert_not_called()

    def test_missing_cli_and_handoff_only_adapter_do_not_spawn(self):
        with patch("aihq.adapters.shutil.which", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing"):
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root)
        with self.assertRaisesRegex(ValueError, "handoff-only"):
            adapters.execute({"adapter": "devin", "prompt": "Review"}, self.root)
        self.popen.assert_not_called()
        self.http.assert_not_called()

    def test_cli_failure_reports_exit_status_and_bounded_output(self):
        self.process_output(b"failure " + b"a" * 10000, returncode=17)
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"):
            with self.assertRaisesRegex(ValueError, "codex exited with 17") as caught:
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root)
        self.assertLess(len(str(caught.exception)), 4100)
        self.assertIn("failure", str(caught.exception))

    def test_cli_empty_output_and_invalid_utf8_are_handled(self):
        for output, expected in ((b"", "Agent completed without text output."), (b"result\xff", "result\ufffd")):
            self.process_output(output)
            with self.subTest(output=output), patch("aihq.adapters.shutil.which", return_value="/mock/claude"):
                self.assertEqual(adapters.execute({"adapter": "claude", "prompt": "Review"}, self.root), expected)

    def test_cli_success_output_is_bounded(self):
        self.process_output(b"a" * 70000)
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"):
            result = adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root)
        self.assertEqual(len(result), 64000)

    @unittest.skipUnless(adapters.os.name == "posix", "POSIX process-group behavior")
    def test_cli_timeout_terminates_group_and_reraises_original_timeout(self):
        process = self.process_output()
        timeout = subprocess.TimeoutExpired("mock-codex", 2)
        process.wait.side_effect = [timeout, 0]
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"), patch("aihq.adapters.os.killpg") as killpg:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root, timeout=2)
        self.assertIs(caught.exception, timeout)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)])
        self.assertEqual([call.kwargs for call in process.wait.call_args_list], [{"timeout": 2}, {"timeout": 5}])

    @unittest.skipUnless(adapters.os.name == "posix", "POSIX process-group behavior")
    def test_cli_timeout_escalates_to_kill_and_reaps_process(self):
        process = self.process_output()
        process.wait.side_effect = [subprocess.TimeoutExpired("mock", 2), subprocess.TimeoutExpired("mock", 5), 0]
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"), patch("aihq.adapters.os.killpg") as killpg:
            with self.assertRaises(subprocess.TimeoutExpired):
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root, timeout=2)
        self.assertEqual([call.args for call in killpg.call_args_list],
                         [(process.pid, signal.SIGTERM), (process.pid, signal.SIGKILL)])
        self.assertEqual(process.wait.call_count, 3)

    @unittest.skipUnless(adapters.os.name == "posix", "POSIX process-group behavior")
    def test_timeout_kills_remaining_descendants_after_parent_has_exited(self):
        process = self.process_output()
        operations = []

        def wait_for_parent(timeout=None):
            operations.append(("wait", timeout))
            if timeout == 2:
                raise subprocess.TimeoutExpired("mock", timeout)
            return 0

        def kill_group(process_id, sig):
            operations.append(("signal", sig))
            self.assertEqual(process_id, process.pid)

        process.wait.side_effect = wait_for_parent
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"), patch("aihq.adapters.os.killpg", side_effect=kill_group):
            with self.assertRaises(subprocess.TimeoutExpired):
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root, timeout=2)
        self.assertEqual(operations, [("wait", 2), ("signal", signal.SIGTERM),
                                      ("wait", 5), ("signal", signal.SIGKILL)])

    @unittest.skipUnless(adapters.os.name == "posix", "POSIX process-group behavior")
    def test_cleanup_of_already_exited_process_group_preserves_original_timeout(self):
        process = self.process_output()
        timeout = subprocess.TimeoutExpired("mock", 2)
        process.wait.side_effect = [timeout, 0]
        with patch("aihq.adapters.shutil.which", return_value="/mock/codex"), patch(
            "aihq.adapters.os.killpg", side_effect=[None, ProcessLookupError("Group already gone")]
        ) as killpg:
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                adapters.execute({"adapter": "codex", "prompt": "Review"}, self.root, timeout=2)
        self.assertIs(caught.exception, timeout)
        self.assertEqual(killpg.call_args.args, (process.pid, signal.SIGKILL))

    def test_ollama_uses_only_fixed_loopback_endpoint_and_requested_model(self):
        connection = self.http.return_value
        response = connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({"choices": [{"message": {"content": " Local answer "}}]}).encode()
        result = adapters.execute({"adapter": "ollama", "prompt": self.prompt, "model": "existing-local-model"}, self.root, timeout=9)
        self.assertEqual(result, "Local answer")
        self.http.assert_called_once_with("127.0.0.1", 11434, timeout=9)
        args = connection.request.call_args.args
        self.assertEqual(args[:2], ("POST", "/v1/chat/completions"))
        body = json.loads(args[2])
        self.assertEqual(body["model"], "existing-local-model")
        self.assertEqual(body["messages"], [{"role": "user", "content": self.prompt}])
        self.assertIs(body["stream"], False)
        self.assertEqual(args[3], {"Content-Type": "application/json"})
        response.read.assert_called_once_with(1048577)
        connection.close.assert_called_once_with()
        self.popen.assert_not_called()

    def test_ollama_http_failure_oversized_output_and_invalid_json_close_connection(self):
        for status, data in ((503, b"unavailable"), (200, b"a" * 1048577), (200, b"not json")):
            self.http.reset_mock()
            connection = self.http.return_value
            response = connection.getresponse.return_value
            response.status, response.read.return_value = status, data
            with self.subTest(status=status, size=len(data)), self.assertRaises(ValueError):
                adapters.execute({"adapter": "ollama", "prompt": "Review"}, self.root)
            connection.close.assert_called_once_with()

    def test_ollama_timeout_or_disconnect_closes_connection_without_fallback(self):
        for exception in (TimeoutError("Mock timeout"), http.client.RemoteDisconnected("Mock disconnect")):
            self.http.reset_mock()
            connection = self.http.return_value
            connection.request.side_effect = exception
            with self.subTest(exception=exception), self.assertRaises(type(exception)):
                adapters.execute({"adapter": "ollama", "prompt": "Review"}, self.root)
            connection.close.assert_called_once_with()
        self.popen.assert_not_called()

    def test_statuses_do_not_claim_discovered_tools_are_authenticated(self):
        with patch("aihq.adapters.shutil.which", side_effect=lambda name: "/mock/codex" if name == "codex" else None):
            statuses = {item["id"]: item["status"] for item in adapters.statuses()}
        self.assertEqual(statuses, {
            "hermes": "missing", "codex": "installed-unverified", "claude": "missing",
            "devin": "handoff-only", "ollama": "requires-local-server",
        })
        self.popen.assert_not_called()
        self.http.assert_not_called()

    def test_binding_changes_with_prompt_model_brain_revision_path_and_binary(self):
        executable = self.root / "mock-codex"
        executable.write_bytes(b"version one")
        brain = Mock()
        brain.fingerprint.return_value = "brain-one"
        brain.revision.return_value = {"head": "revision-one", "dirty": False}
        payload = {"adapter": "codex", "prompt": "Original prompt", "model": "original-model"}
        with patch("aihq.adapters.shutil.which", return_value=str(executable)) as which:
            original = adapters.binding(payload, brain)
            self.assertEqual(adapters.binding(dict(payload), brain), original)
            self.assertNotEqual(adapters.binding({**payload, "prompt": "Changed prompt"}, brain), original)
            self.assertNotEqual(adapters.binding({**payload, "model": "changed-model"}, brain), original)
            brain.fingerprint.return_value = "brain-two"
            self.assertNotEqual(adapters.binding(payload, brain), original)
            brain.fingerprint.return_value = "brain-one"
            brain.revision.return_value = {"head": "revision-two", "dirty": False}
            self.assertNotEqual(adapters.binding(payload, brain), original)
            brain.revision.return_value = {"head": "revision-one", "dirty": False}
            executable.write_bytes(b"version two")
            self.assertNotEqual(adapters.binding(payload, brain), original)
            executable.write_bytes(b"version one")
            alternate = self.root / "other-codex"
            alternate.write_bytes(b"version one")
            which.return_value = str(alternate)
            self.assertNotEqual(adapters.binding(payload, brain), original)
        self.popen.assert_not_called()
        self.http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
