import http.server
import json
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from aihq import core, basetools


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><head><title>T</title><style>p{}</style></head><body><h1>Art Shows</h1><p>Deadline  Nov 30</p><script>x()</script></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class BaseToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.domain = Path(self.tmp.name)
        (self.domain / "hq" / "os").mkdir(parents=True)
        (self.domain / "governance").mkdir()
        (self.domain / "governance" / "CANON.md").write_text("Grades: S / A+ / A\n")
        (self.domain / ".env").write_text("SECRET=1\n")
        (self.domain / "os" / "apps" / "curator").mkdir(parents=True)
        (self.domain / "os" / "apps" / "curator" / "app.json").write_text(json.dumps({"id": "curator", "name": "Curator", "kind": "module"}))
        self.cfg = mock.patch.object(core, "local_config", return_value={"search_scopes": ["governance"]})
        self.cfg.start()
        self.addCleanup(self.cfg.stop)
        self.belt = basetools.BaseToolbelt(self.domain, app_id="art-shows", allow_local_fetch=True, os_root=self.domain / "os")

    def tearDown(self):
        self.tmp.cleanup()

    def test_specs_are_anthropic_shaped(self):
        names = {s["name"] for s in self.belt.specs()}
        self.assertTrue({"read_file", "search_repo", "memory", "write_memory", "inbox", "list_inbox", "log", "fetch_url",
                         "records", "write_record", "list_apps", "create_app", "update_app", "delete_app", "run_app"} <= names)
        for spec in self.belt.specs():
            self.assertEqual(spec["input_schema"]["type"], "object")

    def test_read_and_search_deny_secrets(self):
        self.assertIn("Grades", self.belt.call("read_file", {"path": "governance/CANON.md"}))
        self.assertIn("refused", self.belt.call("read_file", {"path": ".env"}))
        self.assertIn("refused", self.belt.call("read_file", {"path": "../outside"}))
        self.assertIn("governance/CANON.md:1", self.belt.call("search_repo", {"query": "grades"}))

    def test_memory_inbox_log_records_land_under_hq(self):
        self.assertEqual(self.belt.call("write_memory", {"key": "Art Shows", "content": "Texas first."}), "memory written: Art Shows")
        self.assertEqual(self.belt.call("memory", {"key": "art shows"}), "Texas first.")
        self.assertIn("## Art Shows", self.belt.memory_digest())
        out = self.belt.call("inbox", {"kind": "note", "title": "Two new shows", "body": "see board"})
        self.assertTrue(out.startswith("inbox item "))
        items = self.belt.inbox_items()
        self.assertEqual(items[0]["title"], "Two new shows")
        self.assertEqual(items[0]["status"], "open")
        self.assertEqual(items[0]["app"], "art-shows")
        self.assertIn("kind must be", self.belt.call("inbox", {"kind": "approval", "title": "x", "body": "y"}))
        self.assertEqual(self.belt.call("log", {"what": "ran", "evidence": "hq/os"}), "logged")
        log = next((self.domain / "hq" / "log").glob("*.md")).read_text()
        self.assertIn("· art-shows · ran · hq/os", log)
        created = self.belt.call("write_record", {"id": "https://x.org/call", "fields": {"title": "Call", "deadline": "2026-11-30", "note": "fee 35"}})
        self.assertTrue(created.startswith("record created: art-shows/"))
        updated = self.belt.call("write_record", {"id": "https://x.org/call", "fields": {"title": "Call", "status": "entered"}})
        self.assertTrue(updated.startswith("record updated"))
        records = self.belt.records("art-shows")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "entered")
        self.assertEqual(records[0]["body"], "fee 35", "the note survives an update that did not mention it")
        self.assertEqual(self.belt.records("art-shows", "nothing-like-this"), [])
        self.assertIn("Call", self.belt.call("records", {"app": "art-shows"}))

    def test_write_outside_hq_refused(self):
        with self.assertRaises(PermissionError):
            self.belt._writable(self.domain / "governance" / "x.md")
        # ids and app names are slugged, so a traversal attempt lands inside hq/os/records
        item = self.belt.write_record("../../governance", "../x", {"title": "no"})
        self.assertEqual(item["app"], "governance")
        self.assertTrue((self.domain / "hq" / "os" / "records" / "governance" / "x.md").is_file())

    def test_fetch_url_local_server_and_refusals(self):
        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/page"
            text = self.belt.call("fetch_url", {"url": url})
            self.assertIn("Art Shows", text)
            self.assertIn("Deadline Nov 30", text)
            self.assertNotIn("x()", text)
            strict = basetools.BaseToolbelt(self.domain)
            self.assertIn("refused", strict.call("fetch_url", {"url": url}))
            self.assertIn("only http(s)", self.belt.call("fetch_url", {"url": "file:///etc/passwd"}))
        finally:
            server.shutdown()
            server.server_close()

    def test_list_apps_and_app_ops_delegation(self):
        apps = json.loads(self.belt.call("list_apps", {}))
        self.assertEqual(apps[0]["id"], "curator")
        self.assertIn("not available", self.belt.call("create_app", {"id": "x", "name": "X", "prompt": "p"}))
        seen = {}
        belt = basetools.BaseToolbelt(self.domain, app_ops={"create_app": lambda **a: seen.update(a) or {"ok": True, "id": a["id"]}})
        out = belt.call("create_app", {"id": "art-shows", "name": "Art Shows", "prompt": "find shows", "every": "1d"})
        self.assertEqual(seen["every"], "1d")
        self.assertIn('"ok": true', out)


if __name__ == "__main__":
    unittest.main()
