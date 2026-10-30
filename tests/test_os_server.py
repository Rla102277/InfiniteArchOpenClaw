import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from aihq.runtime import Runtime
from aihq.server import MAX_BODY, make_handler

if __package__:
    from .test_brain import brain_fixture, repository_files
else:
    from test_brain import brain_fixture, repository_files


class OSServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()
        self.execution = patch("aihq.adapters.execute", side_effect=AssertionError("No live external execution"))
        self.execute = self.execution.start()
        self.addCleanup(self.execution.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("secret", self.runtime))
        self.server.handle_error = Mock()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", route="/api/os/state", payload=None, headers=None, raw=None):
        request_headers = {"Host": f"127.0.0.1:{self.server.server_port}"}
        body = raw
        if method == "POST":
            request_headers.update({"X-HQ-Token": "secret", "Content-Type": "application/json", "Origin": self.origin})
            if raw is None:
                body = json.dumps(payload).encode()
        if headers:
            request_headers.update(headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, route, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def post(self, payload, headers=None):
        return self.request("POST", "/api/os/action", payload, headers)

    def test_state_action_and_restart_use_only_private_storage(self):
        before = repository_files(self.repo)
        status, state, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(state["brain"]["root"], str(self.repo.resolve()))
        self.assertFalse(state["brain"]["drift"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        status, result, _ = self.post({"action": "memory.put", "key": "owner-note", "content": "Private HTTP note"})
        self.assertEqual(status, 201)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["key"], "owner-note")
        state = self.request()[1]
        self.assertEqual(state["memories"][0]["content"], "Private HTTP note")
        restarted = Runtime(root=self.repo, home=self.home)
        self.assertEqual(restarted.snapshot()["memories"], state["memories"])
        self.assertEqual(repository_files(self.repo), before)
        self.execute.assert_not_called()

    def test_host_rebinding_attempts_are_rejected_for_reads_and_writes(self):
        for host in ("evil.example", "127.0.0.1.evil.example", "localhost.evil.example", "127.0.0.2", ""):
            for method in ("GET", "POST"):
                with self.subTest(host=host, method=method):
                    status, data, _ = self.request(method, payload={"action": "memory.put", "key": "x", "content": "x"},
                                                   route="/api/os/action" if method == "POST" else "/api/os/state",
                                                   headers={"Host": host})
                    self.assertEqual(status, 403)
                    self.assertIn("error", data)
        self.assertEqual(self.runtime.snapshot()["memories"], [])

    def test_cross_origin_and_cross_site_requests_are_rejected(self):
        for headers in (
            {"Origin": "https://evil.example"}, {"Origin": "null"},
            {"Origin": "http://127.0.0.1:1"}, {"Origin": self.origin + ".evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            for method in ("GET", "POST"):
                with self.subTest(headers=headers, method=method):
                    status, data, _ = self.request(method, route="/api/os/action" if method == "POST" else "/api/os/state",
                                                   payload={"action": "memory.put", "key": "x", "content": "x"}, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertIn("error", data)
        self.assertEqual(self.runtime.snapshot()["memories"], [])

    def test_localhost_alias_and_same_origin_are_accepted(self):
        status, _, _ = self.request(headers={"Host": f"localhost:{self.server.server_port}",
                                             "Origin": f"http://localhost:{self.server.server_port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)
        status, token, _ = self.request(route="/api/csrf", headers={"Origin": self.origin})
        self.assertEqual(status, 200)
        self.assertEqual(token, {"token": "secret"})
        status, _, _ = self.post({"action": "memory.put", "key": "allowed", "content": "Local"}, {"X-HQ-Token": token["token"]})
        self.assertEqual(status, 201)

    def test_missing_or_wrong_csrf_token_does_not_mutate_state(self):
        for token in ("", "wrong-secret", "secret-extra"):
            with self.subTest(token=token):
                status, data, _ = self.post({"action": "memory.put", "key": "x", "content": "x"}, {"X-HQ-Token": token})
                self.assertEqual(status, 403)
                self.assertIn("error", data)
        self.assertEqual(self.runtime.snapshot()["memories"], [])

    def test_non_ascii_csrf_token_is_rejected_without_request_crash(self):
        try:
            status, data, _ = self.post({"action": "memory.put", "key": "x", "content": "x"}, {"X-HQ-Token": "s\xe9cret"})
        except http.client.RemoteDisconnected:
            self.fail("A non-ASCII CSRF token crashed the request handler instead of returning 403")
        self.assertEqual(status, 403)
        self.assertIn("error", data)
        self.server.handle_error.assert_not_called()

    def test_invalid_json_types_encoding_and_content_type_return_json_errors(self):
        for raw in (b"{", b"[]", b"null", b'"text"', b"123", b'{"action": "\xff"}'):
            with self.subTest(raw=raw):
                status, data, _ = self.request("POST", "/api/os/action", raw=raw)
                self.assertEqual(status, 400)
                self.assertIn("error", data)
        status, data, _ = self.post({"action": "worker.tick"}, {"Content-Type": "text/plain"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.assertEqual(self.request()[0], 200)
        self.server.handle_error.assert_not_called()

    def test_deeply_nested_json_is_rejected_without_request_crash(self):
        raw = b'{"action":' + b"[" * 1500 + b"0" + b"]" * 1500 + b"}"
        self.assertLess(len(raw), MAX_BODY)
        try:
            status, data, _ = self.request("POST", "/api/os/action", raw=raw)
        except http.client.RemoteDisconnected:
            self.fail("Deeply nested JSON crashed the request handler instead of returning 400")
        self.assertEqual(status, 400)
        self.assertIn("error", data)
        self.server.handle_error.assert_not_called()

    def test_invalid_request_lengths_return_errors_and_server_stays_available(self):
        for length in ("0", "-1", str(MAX_BODY + 1), "not-a-number"):
            with self.subTest(length=length):
                status, data, _ = self.request("POST", "/api/os/action", raw=b"{}", headers={"Content-Length": length})
                self.assertEqual(status, 400)
                self.assertIn("error", data)
        self.assertEqual(self.request()[0], 200)

    def test_bad_action_payloads_do_not_kill_server_or_modify_data(self):
        before = self.runtime.snapshot()
        for payload in (
            {}, {"action": "unknown"}, {"action": "memory.put", "key": [], "content": "bad"},
            {"action": "task.create", "title": "Bad", "operation": "memory.put", "payload": []},
            {"action": "schedule.create", "name": "Bad", "workflow": [], "interval_seconds": 60},
            {"action": "app.lifecycle", "id": "../escape", "transition": "install"},
            {"action": "builder.create", "name": "Unapproved"},
        ):
            with self.subTest(payload=payload):
                status, data, _ = self.post(payload)
                self.assertEqual(status, 400)
                self.assertIn("error", data)
        after = self.request()[1]
        for key in ("tasks", "memories", "schedules", "events"):
            self.assertEqual(after[key], before[key])
        self.assertTrue(self.thread.is_alive())
        self.server.handle_error.assert_not_called()

    def test_http_tick_never_executes_external_even_with_approval_and_override(self):
        status, data, _ = self.post({"action": "task.create", "title": "Review",
                                     "operation": "agent.prompt", "payload": {"adapter": "codex", "prompt": "Review safely"}})
        self.assertEqual(status, 201)
        task_id = data["result"]["id"]
        state = self.request()[1]
        self.assertEqual(state["tasks"][0]["status"], "awaiting_approval")
        approval_id = state["approvals"][0]["id"]
        self.assertEqual(self.post({"action": "approval.decide", "id": approval_id, "decision": "approved"})[0], 201)
        self.assertEqual(self.post({"action": "memory.put", "key": "http", "content": "Works"})[0], 201)
        status, data, _ = self.post({"action": "worker.tick", "allow_external": True})
        self.assertEqual(status, 201)
        self.assertFalse(data["result"]["external_enabled"])
        self.assertEqual(data["result"]["processed"], [])
        task = next(task for task in self.request()[1]["tasks"] if task["id"] == task_id)
        self.assertEqual(task["status"], "ready")
        self.execute.assert_not_called()

    def test_cli_only_recovery_is_blocked_over_http(self):
        task_id = self.runtime.action({"action": "task.create", "title": "Recover later",
                                       "operation": "digest.create", "payload": {}})["id"]
        with self.runtime.connect() as connection:
            connection.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
        status, data, _ = self.post({"action": "task.recover", "id": task_id})
        self.assertEqual(status, 400)
        self.assertIn("CLI", data["error"])
        self.assertEqual(self.request()[1]["tasks"][0]["status"], "running")

    def test_expected_runtime_errors_return_json_and_next_request_succeeds(self):
        for exception in (ValueError("Bad declaration"), OSError("Temporary private store failure")):
            with self.subTest(exception=exception), patch.object(self.runtime, "snapshot", side_effect=exception):
                status, data, _ = self.request()
                self.assertEqual(status, 400)
                self.assertEqual(data["error"], str(exception))
            self.assertEqual(self.request()[0], 200)
        with patch.object(self.runtime, "action", side_effect=ValueError("Bad action")):
            self.assertEqual(self.post({"action": "worker.tick"})[0], 400)
        self.assertEqual(self.post({"action": "memory.put", "key": "still-alive", "content": "Recovered"})[0], 201)
        self.server.handle_error.assert_not_called()

    def test_unknown_os_routes_return_json_404(self):
        self.assertEqual(self.request(route="/api/os/missing")[0], 404)
        status, data, _ = self.request("POST", "/api/os/missing", payload={"action": "worker.tick"})
        self.assertEqual(status, 404)
        self.assertIn("error", data)

    def test_legacy_project_routes_are_gone(self):
        for route in ("/api/projects", "/api/features", "/api/decide"):
            status, data, _ = self.request("POST", route, {"name": "x", "idea": "y"})
            self.assertEqual(status, 404, route)
        status, data, _ = self.request(route="/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("projects", data)
        self.server.handle_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
