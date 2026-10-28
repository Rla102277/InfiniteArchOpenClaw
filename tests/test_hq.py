import json
import os
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from aihq import core, hq, runtime
from aihq.server import make_handler

ROOT = Path(__file__).resolve().parents[1]


def _channel(value: float) -> float:
    value = value / 255
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def contrast(foreground: str, background: str) -> float:
    def lum(color: str) -> float:
        red, green, blue = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        r, g, b = _channel(red), _channel(green), _channel(blue)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    lighter, darker = sorted((lum(foreground), lum(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


class HeadquartersApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(runtime, "RUNTIME_DIR", root / "runtime"),
            patch.object(hq, "PROPOSALS_DIR", root / "proposals"),
            patch.object(core, "PROJECTS", root / "projects"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("test-secret"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def raw(self, method, route, body=None, token=None, host=None):
        import http.client
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {"Host": host or f"127.0.0.1:{self.server.server_port}"}
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-HQ-Token"] = token
        con.request(method, route, body=payload, headers=headers)
        response = con.getresponse()
        status = response.status
        content_type = response.getheader("Content-Type")
        data = response.read()
        con.close()
        return status, data, content_type

    def request(self, method, route, body=None, token=None, host=None):
        status, data, _content_type = self.raw(method, route, body, token, host)
        return status, json.loads(data)

    def test_status_marks_missing_adapter_offline_without_a_heartbeat(self):
        with patch("aihq.providers.shutil.which", return_value=None):
            status, body = self.request("GET", "/api/v1/system/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["provenance"], "live")
        self.assertTrue(body["generated_at"])
        adapters = {item["id"]: item for item in body["data"]["adapters"]}
        self.assertEqual(adapters["local-hq"]["status"], "available")
        self.assertEqual(adapters["hermes"]["status"], "offline")
        self.assertFalse(adapters["hermes"]["available"])
        self.assertEqual(adapters["hermes"]["probe"], "path-lookup")
        self.assertNotIn("heartbeat", adapters["hermes"])
        self.assertIn("offline", adapters["hermes"]["detail"].lower())
        self.assertTrue(adapters["hermes"]["checked_at"])

    def test_scheduled_check_records_offline_hermes_and_history(self):
        with patch("aihq.providers.shutil.which", return_value=None):
            created = self.request("POST", "/api/v1/schedules", {
                "name": "Adapter check",
                "kind": "adapter-status",
            }, "test-secret")
            self.assertEqual(created[0], 201)
            schedule_id = created[1]["data"]["schedule"]["id"]
            listed = self.request("GET", "/api/v1/schedules")
            self.assertEqual(listed[1]["data"][0]["name"], "Adapter check")
            run = self.request("POST", f"/api/v1/schedules/{schedule_id}/run", {}, "test-secret")
            self.assertEqual(run[0], 201)
            self.assertEqual(run[1]["data"]["status"], "completed")
            self.assertIn("Hermes", run[1]["data"]["error"])
            self.assertIn("No heartbeat", run[1]["data"]["error"])
            self.assertTrue(run[1]["data"]["finished_at"])
            agents = self.request("GET", "/api/v1/agents")
            guide = next(item for item in agents[1]["data"] if item["id"] == "repository-guide")
            self.assertEqual(guide["runtime_status"], "offline")
            self.assertEqual(guide["last_error"]["run_id"], run[1]["data"]["id"])
            self.assertEqual(guide["last_error"]["at"], run[1]["data"]["finished_at"])
            universe = self.request("GET", "/api/v1/universe")
            schedules = next(item for item in universe[1]["data"]["domains"] if item["id"] == "schedules")
            card = schedules["children"][0]
            self.assertGreaterEqual(len(card["history"]), 1)
            self.assertEqual(card["history"][0]["status"], "completed")

    def test_running_agent_keeps_an_exact_previous_error(self):
        runtime.insert_run({
            "id": "n-11111111",
            "workspace_id": "local",
            "schedule_id": None,
            "agent_id": "local-clerk",
            "kind": "manual",
            "status": "error",
            "outcome": "The local check failed.",
            "error": "Local clerk could not read a record.",
            "started_at": "2026-10-28T11:00:00+00:00",
            "finished_at": "2026-10-28T11:05:00+00:00",
        })
        runtime.insert_run({
            "id": "n-22222222",
            "workspace_id": "local",
            "schedule_id": None,
            "agent_id": "local-clerk",
            "kind": "manual",
            "status": "running",
            "outcome": "Checking local records.",
            "error": None,
            "started_at": "2026-10-28T12:00:00+00:00",
            "finished_at": None,
        })
        _status, body = self.request("GET", "/api/v1/agents")
        clerk = next(item for item in body["data"] if item["id"] == "local-clerk")
        self.assertEqual(clerk["runtime_status"], "running")
        self.assertEqual(clerk["running_since"], "2026-10-28T12:00:00+00:00")
        self.assertEqual(clerk["last_error"]["message"], "Local clerk could not read a record.")
        self.assertEqual(clerk["last_error"]["at"], "2026-10-28T11:05:00+00:00")

    def test_tick_runs_due_schedules_and_respects_pause(self):
        manual = self.request("POST", "/api/v1/schedules", {"name": "Manual note", "kind": "local-note"}, "test-secret")
        periodic = self.request("POST", "/api/v1/schedules", {
            "name": "Periodic note",
            "kind": "local-note",
            "cadence_seconds": 60,
        }, "test-secret")
        self.assertEqual(periodic[0], 201)
        periodic_id = periodic[1]["data"]["schedule"]["id"]
        hq.tick("2999-01-01T00:00:00+00:00")
        hq.tick("2999-03-01T00:00:00+00:00")
        runs = self.request("GET", "/api/v1/runs")[1]["data"]
        self.assertEqual(sum(1 for item in runs if item["schedule_id"] == periodic_id), 2)
        self.assertFalse(any(item["schedule_id"] == manual[1]["data"]["schedule"]["id"] for item in runs))
        paused = self.request("PATCH", f"/api/v1/schedules/{periodic_id}", {"enabled": False}, "test-secret")
        self.assertEqual(paused[0], 200)
        hq.tick("2999-06-01T00:00:00+00:00")
        runs = self.request("GET", "/api/v1/runs")[1]["data"]
        self.assertEqual(sum(1 for item in runs if item["schedule_id"] == periodic_id), 2)

    def test_external_action_requires_approval_and_does_not_execute(self):
        with patch("aihq.hq.subprocess.run") as spy:
            created = self.request("POST", "/api/v1/schedules", {
                "name": "Send nothing",
                "kind": "external-action",
            }, "test-secret")
            schedule_id = created[1]["data"]["schedule"]["id"]
            approval_id = created[1]["data"]["approval_id"]
            pending = self.request("GET", "/api/v1/approvals")
            self.assertTrue(any(item["id"] == approval_id and item["status"] == "pending" for item in pending[1]["data"]))
            blocked = self.request("POST", f"/api/v1/schedules/{schedule_id}/run", {}, "test-secret")
            self.assertEqual(blocked[1]["data"]["status"], "blocked")
            decision = self.request("POST", f"/api/v1/approvals/{approval_id}/decision", {"decision": "approved"}, "test-secret")
            self.assertEqual(decision[0], 200)
            finished = self.request("POST", f"/api/v1/schedules/{schedule_id}/run", {}, "test-secret")
            self.assertEqual(finished[1]["data"]["status"], "not_executed")
            self.assertIn("did not execute", finished[1]["data"]["error"])
            spy.assert_not_called()

    def test_project_review_decision_still_does_not_merge(self):
        core.create_project("Helpful Bot", "Helpful idea")
        evidence = core.PROJECTS / "helpful-bot" / "evidence" / "test.log"
        evidence.write_text("Command: python -m unittest\nExit code: 0\n", encoding="utf-8")
        review = core.request_review("helpful-bot", "Demo", "evidence/test.log", "feature/demo")
        listed = self.request("GET", "/api/v1/approvals")
        self.assertTrue(any(item["id"] == review["id"] and item["source"] == "project-review" for item in listed[1]["data"]))
        decided = self.request("POST", f"/api/v1/approvals/{review['id']}/decision", {"decision": "accepted"}, "test-secret")
        self.assertEqual(decided[0], 200)
        self.assertEqual(core.state()["pending"], [])

    def test_workspace_isolation_and_theme_restore(self):
        core.create_project("Helpful Bot", "Helpful idea")
        created = self.request("POST", "/api/v1/tasks", {
            "title": "Project task",
            "detail": "Only for this workspace",
            "workspace_id": "helpful-bot",
        }, "test-secret")
        self.assertEqual(created[0], 201)
        local_tasks = self.request("GET", "/api/v1/tasks")
        self.assertEqual(local_tasks[1]["data"], [])
        project_tasks = self.request("GET", "/api/v1/tasks?workspace=helpful-bot")
        self.assertEqual(project_tasks[1]["data"][0]["title"], "Project task")
        self.request("PUT", "/api/v1/preferences/theme", {"theme": "dark"}, "test-secret")
        prefs = self.request("PUT", "/api/v1/preferences", {
            "layout": {"domain": "agents", "selected": "local-clerk"},
        }, "test-secret")
        self.assertEqual(prefs[1]["data"]["theme"], "dark")
        self.assertEqual(prefs[1]["data"]["layout"]["domain"], "agents")
        again = self.request("GET", "/api/v1/tasks?workspace=helpful-bot")
        self.assertEqual(len(again[1]["data"]), 1)

    def test_demo_mode_hides_real_records_and_rejects_writes(self):
        core.create_project("Secret Plan", "A private idea that must not appear in demo mode")
        self.request("POST", "/api/v1/tasks", {"title": "Real task", "detail": "Real detail"}, "test-secret")
        turned_on = self.request("PUT", "/api/v1/preferences", {"demo": True}, "test-secret")
        self.assertEqual(turned_on[1]["data"]["demo"], True)
        universe = self.request("GET", "/api/v1/universe")
        blob = json.dumps(universe[1]).lower()
        self.assertEqual(universe[1]["provenance"], "demo")
        self.assertIn("sample", blob)
        self.assertNotIn("secret", blob)
        self.assertNotIn("grill-me", blob)
        self.assertNotIn("real task", blob)
        self.assertTrue(all(domain["provenance"] == "demo" for domain in universe[1]["data"]["domains"]))
        blocked = self.request("POST", "/api/v1/tasks", {"title": "Nope", "detail": "Nope"}, "test-secret")
        self.assertEqual(blocked[0], 409)
        live = self.request("GET", "/api/v1/universe")
        self.assertEqual(live[1]["provenance"], "demo")

    def test_live_universe_includes_versioned_skills(self):
        universe = self.request("GET", "/api/v1/universe")
        knowledge = next(item for item in universe[1]["data"]["domains"] if item["id"] == "knowledge")
        names = [child["name"] for child in knowledge["children"]]
        self.assertIn("grill-me", names)
        self.assertEqual(knowledge["provenance"], "live")

    def test_unavailable_task_store_is_not_reported_as_zero(self):
        def boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("disk")

        with patch.object(runtime, "list_tasks", boom):
            universe = self.request("GET", "/api/v1/universe")
        tasks = next(item for item in universe[1]["data"]["domains"] if item["id"] == "tasks")
        knowledge = next(item for item in universe[1]["data"]["domains"] if item["id"] == "knowledge")
        self.assertEqual(tasks["provenance"], "unavailable")
        self.assertEqual(tasks["children"], [])
        self.assertIn("unavailable", json.dumps(tasks["kpis"]).lower())
        self.assertEqual(knowledge["provenance"], "live")

    def test_agent_proposal_is_a_git_file_and_not_installed(self):
        canonical = (ROOT / "system" / "agents" / "repository-guide.json").read_text(encoding="utf-8")
        created = self.request("POST", "/api/v1/agents", {
            "name": "Research helper",
            "purpose": "Summarize repository documents into a handoff.",
            "adapter": "local-hq",
            "locality": "local",
        }, "test-secret")
        self.assertEqual(created[0], 201)
        self.assertFalse(created[1]["data"]["pushed"])
        self.assertFalse(created[1]["data"]["agent"]["installed"])
        json_path = hq.PROPOSALS_DIR / "research-helper.json"
        markdown = (hq.PROPOSALS_DIR / "research-helper.md").read_text(encoding="utf-8")
        self.assertTrue(json_path.is_file())
        self.assertIn("not pushed", markdown.lower())
        self.assertIn("local-hq", markdown)
        agents = self.request("GET", "/api/v1/agents")[1]["data"]
        proposal = next(item for item in agents if item["id"] == "research-helper")
        self.assertEqual(proposal["runtime_status"], "proposed")
        self.assertEqual(proposal["origin"], "proposed")
        self.assertEqual((ROOT / "system" / "agents" / "repository-guide.json").read_text(encoding="utf-8"), canonical)

    def test_messages_do_not_launch_adapters(self):
        refused = self.request("POST", "/api/v1/conversations/command/messages", {
            "text": "Open the agents",
            "provider": "hermes",
            "locality": "local",
        }, "test-secret")
        self.assertEqual(refused[0], 400)
        self.assertIn("Local-only", refused[1]["error"])
        local = self.request("POST", "/api/v1/conversations/command/messages", {
            "text": "Show agents",
            "provider": "local-hq",
            "locality": "local",
        }, "test-secret")
        self.assertEqual(local[0], 201)
        self.assertEqual(local[1]["data"]["result"]["status"], "completed")
        self.assertIn("did not call a model", local[1]["data"]["result"]["text"])
        self.assertFalse(local[1]["data"]["sent_externally"])
        self.request("PUT", "/api/v1/preferences", {"locality_mode": "provider-choice"}, "test-secret")
        with patch("aihq.providers.shutil.which", return_value=None), patch("aihq.hq.subprocess.run") as spy:
            offline = self.request("POST", "/api/v1/conversations/command/messages", {
                "text": "Hello",
                "provider": "hermes",
                "locality": "local",
            }, "test-secret")
            self.assertEqual(offline[1]["data"]["result"]["status"], "offline")
            self.assertIn("offline", offline[1]["data"]["result"]["text"].lower())
            spy.assert_not_called()
        with patch("aihq.providers.shutil.which", return_value="/usr/bin/hermes"), patch("aihq.hq.subprocess.run") as spy:
            queued = self.request("POST", "/api/v1/conversations/command/messages", {
                "text": "Please do the work",
                "provider": "hermes",
                "locality": "local",
            }, "test-secret")
            self.assertEqual(queued[1]["data"]["result"]["status"], "queued")
            self.assertIn("did not start the adapter", queued[1]["data"]["result"]["text"])
            spy.assert_not_called()

    def test_reconciliation_is_read_only(self):
        with patch("aihq.hq.subprocess.run", wraps=subprocess.run) as spy:
            status, body = self.request("GET", "/api/v1/reconciliation")
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["pushed"])
        self.assertIn("does not commit or push", body["data"]["note"])
        for call in spy.call_args_list:
            command = call.args[0]
            self.assertNotIn("push", command)
            self.assertNotIn("commit", command)
        self.request("PUT", "/api/v1/preferences", {"demo": True}, "test-secret")
        hidden = self.request("GET", "/api/v1/reconciliation")
        self.assertEqual(hidden[0], 409)

    def test_events_can_be_read_incrementally(self):
        self.request("POST", "/api/v1/tasks", {"title": "Noted", "detail": "A local note"}, "test-secret")
        future = self.request("GET", "/api/v1/events?since=2999-01-01T00:00:00+00:00")
        self.assertEqual(future[1]["data"], [])
        past = self.request("GET", "/api/v1/events?since=1999-01-01T00:00:00+00:00")
        self.assertTrue(any(item["kind"] == "task_created" for item in past[1]["data"]))
        invalid = self.request("GET", "/api/v1/events?since=yesterday")
        self.assertEqual(invalid[0], 400)

    def test_writes_require_localhost_and_a_token(self):
        self.assertEqual(self.request("POST", "/api/v1/tasks", {"title": "X", "detail": "Y"})[0], 403)
        self.assertEqual(self.request("GET", "/api/v1/system/status", host="evil.example")[0], 403)
        self.assertEqual(self.request("PUT", "/api/v1/preferences/theme", {"theme": "nope"}, "test-secret")[0], 400)

    def test_command_center_assets_and_feature_flag(self):
        status, payload, content_type = self.raw("GET", "/command")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        text = payload.decode("utf-8").lower()
        self.assertIn("infinite arch", text)
        self.assertNotIn("fitz", text)
        script = self.raw("GET", "/command.js")[1].decode("utf-8").lower()
        self.assertNotIn("innerhtml", script)
        self.assertNotIn("fitz", script)
        worker = self.raw("GET", "/sw.js")[1].decode("utf-8")
        self.assertIn("/api/", worker)
        icon = self.raw("GET", "/icon-192.png")
        self.assertEqual(icon[0], 200)
        self.assertTrue(icon[1].startswith(b"\x89PNG"))
        manifest = json.loads(self.raw("GET", "/manifest.webmanifest")[1])
        self.assertEqual(manifest["name"], "Infinite Arch HQ")
        self.assertNotIn("fitz", json.dumps(manifest).lower())
        with patch.dict(os.environ, {"HQ_COMMAND_CENTER": "0"}):
            self.assertEqual(self.request("GET", "/command")[0], 404)

    def test_theme_tokens_meet_contrast_and_new_files_avoid_forbidden_words(self):
        catalog = json.loads((ROOT / "system" / "themes.json").read_text(encoding="utf-8"))
        css = (ROOT / "web" / "themes" / "themes.css").read_text(encoding="utf-8").lower()
        for foreground, background in catalog["contrast_pairs"]:
            self.assertIn(foreground.lower(), css)
            self.assertIn(background.lower(), css)
            self.assertGreaterEqual(contrast(foreground, background), 4.5)
        checked = [
            ROOT / "aihq" / "hq.py",
            ROOT / "aihq" / "api.py",
            ROOT / "aihq" / "providers.py",
            ROOT / "aihq" / "runtime.py",
            ROOT / "web" / "command.js",
            ROOT / "web" / "command.css",
            ROOT / "web" / "command.html",
            ROOT / "web" / "themes" / "themes.css",
            ROOT / "docs" / "HQ_COMMAND_CENTER.md",
            ROOT / "system" / "demo" / "fixtures.json",
        ]
        for path in checked:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("\u2014", text, path.name)
            self.assertNotIn("fitz", text.lower(), path.name)


if __name__ == "__main__":
    unittest.main()
