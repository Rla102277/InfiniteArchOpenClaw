"""Module apps: manifest validation, loading, delegation, scheduling, static serving, isolation."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from aihq import modules
from aihq.brain import Brain
from aihq.runtime import Runtime
from aihq.server import make_handler

if __package__:
    from .test_brain import brain_fixture
else:
    from test_brain import brain_fixture

APP_PY = '''
import json
from pathlib import Path

class App:
    id = "sample"
    def __init__(self, host):
        self.host = host
        self.ran = []
        self.marker = Path(host.domain) / "ran.json"   # apps/ is brain; real apps write under hq/
    def snapshot(self):
        return {"hello": "world", "counts": {"inbox_open": 3}}
    def action(self, payload):
        if payload.get("action") == "boom":
            raise RuntimeError("kaboom")
        return {"echo": payload}
    def ask(self, text, agent=None):
        return {"answer": f"{agent or 'auto'}: {text}"}
    def routines(self):
        return [{"id": "nightly", "name": "Nightly tidy", "every": "6h"},
                {"id": "morning", "name": "Morning brief", "at": "06:30", "agent": "operator"}]
    def run_routine(self, routine_id):
        self.ran.append(routine_id)
        self.marker.write_text(json.dumps(self.ran))
        return {"ran": routine_id}
    def agents(self):
        return [{"id": "critic", "name": "Critic", "role": "grades", "description": "one frame"}]
    def usage(self):
        return {"today": 10, "7d": 70, "30d": 300, "runs_30d": 4, "by_agent": {"critic": 10}}
    def cli(self, argv):
        print("cli:" + " ".join(argv))
        return 0
    def doctor(self):
        return [{"status": "ok", "name": "sample", "detail": "fine"}]

def create(host):
    return App(host)
'''

MANIFEST = {
    "schema_version": 1, "id": "sample", "name": "Sample", "description": "A module app for tests.",
    "kind": "module", "entry": "sample.app", "icon": "S",
    "views": [{"id": "home", "label": "Home"}, {"id": "inbox", "label": "Inbox", "count": "inbox_open"}],
    "web": {"script": "web/module.js", "style": "web/module.css"},
    "permissions": ["hq.write"], "version": "1.0.0",
}


def add_module(repo: Path, manifest=MANIFEST, app_py=APP_PY, app_id="sample"):
    folder = repo / "apps" / app_id
    (folder / "sample").mkdir(parents=True)
    (folder / "sample" / "__init__.py").write_text("")
    (folder / "sample" / "app.py").write_text(app_py)
    (folder / "web").mkdir()
    (folder / "web" / "module.js").write_text("window.OSModules = window.OSModules || {}; window.OSModules.sample = {};\n")
    (folder / "web" / "module.css").write_text("#module-sample { color: red; }\n")
    (folder / "app.json").write_text(json.dumps({**manifest, "id": app_id}, indent=2) + "\n")
    return folder


class ModuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        add_module(self.repo)
        modules.invalidate()
        self.addCleanup(modules.invalidate)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()
        self.execution = patch("aihq.adapters.execute", side_effect=AssertionError("No live external execution"))
        self.execution.start()
        self.addCleanup(self.execution.stop)

    def test_catalog_accepts_a_module_manifest_and_rejects_bad_ones(self):
        catalog = Brain(self.repo).catalog()
        self.assertIn("sample", {item["id"] for item in catalog["apps"]})
        for bad in ({"entry": "../evil"}, {"entry": "missing.app"}, {"web": {"script": "../x.js"}},
                    {"permissions": ["shell"]}, {"views": []}, {"extra": True}):
            with self.subTest(bad=bad):
                broken = {**MANIFEST, **bad}
                (self.repo / "apps" / "sample" / "app.json").write_text(json.dumps(broken))
                with self.assertRaises(ValueError):
                    Brain(self.repo).catalog()
        (self.repo / "apps" / "sample" / "app.json").write_text(json.dumps(MANIFEST))

    def test_load_snapshot_and_delegation(self):
        loaded = modules.load(self.runtime.brain, self.runtime)
        self.assertEqual(loaded["sample"].id, "sample")
        snapshot = self.runtime.snapshot()
        self.assertEqual(snapshot["modules"]["sample"]["snapshot"]["hello"], "world")
        self.assertEqual(snapshot["modules"]["sample"]["snapshot"]["counts"], {"inbox_open": 3})
        self.assertEqual(snapshot["modules"]["sample"]["manifest"]["entry"], "sample.app")
        app = next(item for item in snapshot["apps"] if item["id"] == "sample")
        self.assertEqual(app["status"], "enabled")
        result = self.runtime.action({"action": "module.action", "app": "sample", "payload": {"action": "ping", "n": 1}})
        self.assertEqual(result, {"echo": {"action": "ping", "n": 1}})
        answer = self.runtime.action({"action": "module.ask", "app": "sample", "text": "hello", "agent": "critic"})
        self.assertEqual(answer["answer"], "critic: hello")
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "module.action", "app": "sample", "payload": {"action": "boom"}})
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "app.lifecycle", "id": "sample", "transition": "uninstall"})

    def test_agents_routines_and_usage_appear_in_the_workspace(self):
        snapshot = self.runtime.snapshot()
        agent = next(item for item in snapshot["agents"] if item["id"] == "sample:critic")
        self.assertEqual(agent["app"], "sample")
        routine_cards = [card for card in snapshot["views"]["routines"]["cards"] if card["id"].startswith("mod-sample-")]
        self.assertEqual({card["module"]["routine"] for card in routine_cards}, {"nightly", "morning"})
        usage = next(card for card in snapshot["views"]["usage"]["cards"] if card["id"] == "use-sample")
        self.assertIn(["Last 30 days", "300"], usage["fields"])

    def test_declared_cadence_becomes_a_schedule_that_runs_in_the_worker(self):
        schedules = {item["workflow"]: item for item in self.runtime.snapshot()["schedules"]}
        self.assertIn("module:sample:nightly", schedules)
        self.assertEqual(schedules["module:sample:nightly"]["interval_seconds"], 6 * 3600)
        self.assertIn("module:sample:morning", schedules)
        self.assertEqual(schedules["module:sample:morning"]["interval_seconds"], 86400)
        count_before = len(schedules)
        self.runtime.reconcile()
        self.assertEqual(len(self.runtime.snapshot()["schedules"]), count_before)
        result = self.runtime.tick(now=time.time() + 7 * 3600)
        self.assertTrue(result["processed"])
        marker = self.repo.parent / "ran.json"
        self.assertIn("nightly", json.loads(marker.read_text()))
        tasks = [t for t in self.runtime.snapshot()["tasks"] if t["operation"] == "module.routine"]
        self.assertEqual(tasks[0]["status"], "succeeded")

    def test_run_now_and_enqueue_through_host(self):
        result = self.runtime.action({"action": "module.routine", "app": "sample", "routine": "morning"})
        self.assertEqual(result, {"ran": "morning"})
        app = modules.get(self.runtime.brain, "sample", self.runtime)
        queued = app.host.enqueue("Tidy later", "nightly")
        self.assertEqual(queued["status"], "ready")
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "task.create", "title": "x", "operation": "module.routine",
                                 "payload": {"app": "sample", "routine": "nope"}})

    def test_broken_module_reports_error_and_dashboard_survives(self):
        (self.repo / "apps" / "sample" / "sample" / "app.py").write_text("raise ImportError('broken on purpose')\n")
        self.runtime.reconcile()
        snapshot = self.runtime.snapshot()
        self.assertIn("broken on purpose", snapshot["modules"]["sample"]["snapshot"]["error"])
        self.assertTrue(snapshot["apps"])
        with self.assertRaises(ValueError):
            self.runtime.action({"action": "module.action", "app": "sample", "payload": {}})

    def test_doctor_rows(self):
        rows = modules.doctor(self.runtime.brain, self.runtime)
        self.assertIn({"status": "ok", "name": "sample: sample", "detail": "fine"}, rows)


class ModuleServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        add_module(self.repo)
        modules.invalidate()
        self.addCleanup(modules.invalidate)
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("secret", self.runtime))
        self.server.handle_error = Mock()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.port = self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        base = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            base.update({"Content-Type": "application/json", "X-HQ-Token": "secret", "Origin": f"http://127.0.0.1:{self.port}"})
        base.update(headers or {})
        connection.request(method, path, json.dumps(body) if body is not None else None, base)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, response.getheader("Content-Type"), data

    def test_static_assets_only_under_web(self):
        status, mime, data = self.call("GET", "/apps/sample/web/module.js")
        self.assertEqual((status, mime), (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"OSModules", data)
        status, mime, _ = self.call("GET", "/apps/sample/web/module.css")
        self.assertEqual((status, mime), (200, "text/css; charset=utf-8"))
        for bad in ("/apps/sample/app.json", "/apps/sample/sample/app.py", "/apps/sample/web/../app.json",
                    "/apps/sample/../../AGENTS.md", "/apps/nope/web/module.js"):
            with self.subTest(path=bad):
                self.assertEqual(self.call("GET", bad)[0], 404)

    def test_api_snapshot_action_and_ask(self):
        status, _, data = self.call("GET", "/api/apps/sample/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["counts"], {"inbox_open": 3})
        status, _, data = self.call("POST", "/api/apps/sample/action", {"action": "ping"})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["result"], {"echo": {"action": "ping"}})
        status, _, data = self.call("POST", "/api/apps/sample/ask", {"text": "hi"})
        self.assertEqual(json.loads(data)["result"]["answer"], "auto: hi")
        status, _, _ = self.call("POST", "/api/apps/sample/action", {"action": "ping"}, {"X-HQ-Token": "wrong"})
        self.assertEqual(status, 403)
        self.assertEqual(self.call("GET", "/api/apps/nope/snapshot")[0], 400)


if __name__ == "__main__":
    unittest.main()
