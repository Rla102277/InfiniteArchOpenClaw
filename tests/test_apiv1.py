"""The read-only /api/v1 views and the WHOPR theme."""
import json
import re
import unittest
from pathlib import Path

from aihq import apiv1

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ("status", "apps", "agents", "tasks", "schedules", "runs", "events",
             "approvals", "conversations", "themes")
SECRETISH = re.compile(r"(?i)(secret|token|api[_-]?key|password|prompt_hash|credential)")


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk(v)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _snapshot():
    return {
        "version": "1.1.0", "storage": "private",
        "brain": {"branch": "main", "head": "abc", "reconciled_head": "abc", "drift": False},
        "adapters": [{"id": "hermes", "label": "Hermes", "available": False, "detail": "not found"}],
        "capabilities": [{"name": "Local operations", "status": "working", "detail": "x"}],
        "telegram": {"running": False},
        "apps": [{"id": "x", "name": "X", "status": "installed"}],
        "modules": {"m1": {"id": "m1", "name": "M1", "counts": {"records": 3}}},
        "agents": [{"id": "a1", "label": "Agent", "role": "r", "adapter": "hermes",
                    "prompt": "never surface this"}],
        "tasks": [{"id": "t1", "title": "Job", "operation": "digest.create", "status": "succeeded",
                   "payload": {"prompt": "secret prompt"}, "result": "ok", "created": "now"}],
        "schedules": [{"id": "s1", "name": "Digest", "workflow": "daily-digest",
                       "interval_seconds": 86400, "enabled": True, "next_run": "soon",
                       "history": [{"status": "succeeded", "at": "t", "detail": "ok"}]}],
        "events": [{"time": "t", "event": "e", "message": "m"}],
        "activity": [{"time": "t", "sentence": "did a thing"}],
        "approvals": [{"id": "ap", "task_id": "t1", "title": "Do it", "status": "pending",
                       "adapter": "hermes", "prompt_hash": "BOUND-HASH"}],
        "chats": [{"id": "c1", "title": "Thread", "agent": "operator", "messages": 2}],
        "themes": [{"id": "whopr"}],
        "preferences": {"theme": "whopr"},
    }


class ApiV1Tests(unittest.TestCase):
    def test_every_resource_builds(self):
        snap = _snapshot()
        for name in RESOURCES:
            with self.subTest(name=name):
                data = apiv1.envelope(name, snap)
                self.assertTrue(data["ok"])
                self.assertEqual(data["provenance"], "live")
                self.assertFalse(data["demo"])
                self.assertIn("data", data)

    def test_unknown_resource_raises(self):
        with self.assertRaises(ValueError):
            apiv1.envelope("memory", _snapshot())

    def test_no_secret_or_prompt_like_values(self):
        snap = _snapshot()
        for name in RESOURCES:
            data = apiv1.envelope(name, snap)
            for key in _walk(data):
                self.assertNotRegex(key, SECRETISH, f"{name}.{key}")
            blob = json.dumps(data)
            self.assertNotIn("secret prompt", blob)
            self.assertNotIn("BOUND-HASH", blob)

    def test_tasks_drop_payload_and_approvals_drop_hash(self):
        data = apiv1.envelope("tasks", _snapshot())
        self.assertNotIn("payload", data["data"]["tasks"][0])
        data = apiv1.envelope("approvals", _snapshot())
        self.assertNotIn("prompt_hash", data["data"]["approvals"][0])


def _ratio(fg: str, bg: str) -> float:
    def lum(hx):
        c = [int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = max(lum(fg), lum(bg)), min(lum(fg), lum(bg))
    return (hi + 0.05) / (lo + 0.05)


class ThemeContrastTests(unittest.TestCase):
    def test_all_themes_meet_wcag_aa(self):
        for path in sorted((ROOT / "themes").glob("*.json")):
            theme = json.loads(path.read_text())
            colors = theme["colors"]
            for fg_name in ("text", "muted"):
                for bg_name in ("background", "panel"):
                    ratio = _ratio(colors[fg_name], colors[bg_name])
                    self.assertGreaterEqual(ratio, 4.5,
                        f"{theme['id']}: {fg_name} on {bg_name} is {ratio:.2f}:1")
            self.assertGreaterEqual(_ratio(colors["accent"], colors["background"]), 3.0,
                f"{theme['id']}: accent on background fails 3:1")

    def test_whopr_theme_loads_in_catalog(self):
        theme = json.loads((ROOT / "themes" / "whopr.json").read_text())
        self.assertEqual(set(theme["colors"]), {"background", "panel", "text", "muted", "accent"})
        self.assertTrue(all(re.fullmatch(r"#[0-9a-fA-F]{6}", v) for v in theme["colors"].values()))


if __name__ == "__main__":
    unittest.main()


import http.client
import threading
from http.server import ThreadingHTTPServer

from aihq.server import make_handler


class _FakeRuntime:
    def snapshot(self):
        return _snapshot()


class ApiV1RouteTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler('test-secret', runtime=_FakeRuntime()))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, route, body=None, host=None):
        con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
        headers = {'Host': host or f'127.0.0.1:{self.server.server_port}'}
        if body is not None:
            body = json.dumps(body)
            headers['Content-Type'] = 'application/json'
        con.request(method, route, body=body, headers=headers)
        response = con.getresponse()
        status = response.status
        payload = response.read()
        con.close()
        return status, json.loads(payload)

    def test_each_endpoint_200_json_on_loopback(self):
        for name in RESOURCES:
            with self.subTest(name=name):
                status, data = self.request('GET', '/api/v1/' + name)
                self.assertEqual(status, 200)
                self.assertTrue(data["ok"])
                self.assertIn("data", data)

    def test_unknown_resource_404(self):
        self.assertEqual(self.request('GET', '/api/v1/bogus')[0], 404)

    def test_bad_host_rejected(self):
        self.assertEqual(self.request('GET', '/api/v1/status', host='evil.example')[0], 403)

    def test_post_refused(self):
        self.assertEqual(self.request('POST', '/api/v1/tasks', {'x': 1})[0], 403)

    def test_command_assets_served(self):
        for route in ('/command', '/command.js', '/command.css'):
            con = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
            con.request('GET', route, headers={'Host': f'127.0.0.1:{self.server.server_port}'})
            resp = con.getresponse()
            resp.read()
            con.close()
            self.assertEqual(resp.status, 200, route)


if __name__ == "__main__":
    unittest.main()
