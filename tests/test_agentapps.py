"""Agent apps: data-defined apps under hq/os/apps, their runner, the OS wiring and the CLI."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq import agentapps, cli, modules
from aihq.runtime import Runtime

if __package__:
    from .test_brain import brain_fixture
else:
    from test_brain import brain_fixture

PROMPT = "Find juried photography shows in Texas. Record title, deadline, fee and the official url. Never invent a date."
SPEC = {"id": "shows", "name": "Shows", "description": "test app", "every": "1d",
        "fields": ["title", "deadline", "fee", "url"], "key": "url", "web": True, "notify": True}


class FakeModel:
    name = "stub"
    web = False


class FakeLLM:
    @staticmethod
    def pick(kind, web=False):
        model = FakeModel()
        model.web = web
        return model


class FakeAgentRun:
    """Pretends to be the loop: writes two records through the toolbelt, then answers."""
    calls = []

    @staticmethod
    def run(model, system, messages, toolbelt, max_steps=16, on_step=None):
        FakeAgentRun.calls.append({"system": system, "messages": messages, "tools": [s["name"] for s in toolbelt.specs()]})
        names = {s["name"] for s in toolbelt.specs()}
        tool_calls = []
        if "write_record" in names and messages and "Run your routine" in str(messages[-1].get("content", "")):
            for item in ({"title": "Texas Photo Salon", "deadline": "2026-12-01", "fee": "$35", "url": "https://example.org/salon"},
                         {"title": "Frisco Arts Fair", "deadline": "unknown", "fee": "free", "url": "https://example.org/fair"}):
                tool_calls.append({"name": "write_record", "args": item, "result": toolbelt.call("write_record", item)})
        return {"answer": "Verdict: two new calls. Best fit is the Salon. Nearest deadline 2026-12-01.",
                "tool_calls": tool_calls, "usage": {"input": 120, "output": 40}, "stop": "end_turn", "seconds": 0.1}


class FakeChats:
    """Module-style chats with domain first, kept in memory per test."""
    store = {}

    @staticmethod
    def create(domain, title, agent, app=None):
        thread_id = f"t{len(FakeChats.store) + 1}"
        FakeChats.store[thread_id] = {"id": thread_id, "title": title, "agent": agent, "app": app, "messages": []}
        return FakeChats.store[thread_id]

    @staticmethod
    def append(domain, thread_id, role, text, images=None, tool_calls=None, usage=None):
        FakeChats.store[thread_id]["messages"].append({"role": role, "content": text})

    @staticmethod
    def history(domain, thread_id, limit_chars=24000):
        return list(FakeChats.store[thread_id]["messages"])

    @staticmethod
    def get(domain, thread_id):
        return FakeChats.store.get(thread_id)

    @staticmethod
    def list(domain, app=None):
        return [t for t in FakeChats.store.values() if app is None or t["app"] == app]

    @staticmethod
    def needs_summary(domain, thread_id):
        return False

    @staticmethod
    def set_summary(domain, thread_id, text):
        FakeChats.store[thread_id]["summary"] = text


DEPS = {"llm": FakeLLM, "agentrun": FakeAgentRun, "chats": FakeChats, "basetools": None}


class AgentAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.domain = Path(self.temporary.name)
        (self.domain / "hq" / "os" / "memory").mkdir(parents=True)
        (self.domain / "hq" / "os" / "memory" / "overview.md").write_text('---\nkey: "overview"\nupdated: 1\n---\nRandy shoots thresholds.\n', encoding="utf-8")
        FakeAgentRun.calls = []
        FakeChats.store = {}
        agentapps.invalidate()

    def test_validate_normalizes_and_refuses_bad_specs(self):
        spec = agentapps.validate(SPEC)
        self.assertEqual(spec["fields"], ["title", "deadline", "fee", "url", "status", "note"])
        self.assertEqual((spec["key"], spec["kind"], spec["model"]), ("url", "agent", "default"))
        for bad in ({**SPEC, "id": "Bad Id"}, {**SPEC, "key": "venue"}, {**SPEC, "every": "soon"},
                    {**SPEC, "every": "1d", "at": "06:30"}, {**SPEC, "fields": []}, {**SPEC, "model": "huge"}):
            with self.assertRaises(ValueError):
                agentapps.validate(bad)

    def test_create_load_update_delete_live_in_hq(self):
        app = agentapps.create(self.domain, SPEC, PROMPT)
        self.assertTrue((self.domain / "hq/os/apps/shows/app.json").is_file())
        self.assertTrue((self.domain / "hq/os/apps/shows/prompt.md").is_file())
        self.assertEqual(list(agentapps.load(self.domain)), ["shows"])
        with self.assertRaises(ValueError):
            agentapps.create(self.domain, SPEC, PROMPT)
        with self.assertRaises(ValueError):
            agentapps.create(self.domain, {**SPEC, "id": "thin"}, "too short")
        updated = agentapps.update(self.domain, "shows", {"at": "06:45"}, PROMPT + " Texas first.")
        self.assertEqual((updated.spec["at"], updated.spec["every"]), ("06:45", None))
        self.assertIn("Texas first.", updated.prompt)
        target = agentapps.delete(self.domain, "shows")
        self.assertTrue(target.is_dir() and "_deleted" in str(target))
        self.assertEqual(agentapps.load(self.domain), {})
        log = (self.domain / "hq" / "log").glob("*.md")
        self.assertTrue(any("agent app created: shows" in p.read_text() for p in log))

    def test_records_add_update_by_key_never_duplicate(self):
        app = agentapps.create(self.domain, SPEC, PROMPT)
        record, outcome = app.write_record({"title": "Salon", "url": "https://example.org/salon", "fee": "$35"})
        self.assertEqual(outcome, "created")
        self.assertEqual(record["status"], "new")
        _, again = app.write_record({"title": "Salon", "url": "https://example.org/salon", "fee": "$35"})
        self.assertEqual(again, "unchanged")
        changed, outcome = app.write_record({"url": "https://example.org/salon", "fee": "$40"})
        self.assertEqual((outcome, changed["fee"], changed["title"]), ("updated", "$40", "Salon"))
        self.assertEqual(len(app.records()), 1)
        self.assertEqual(app.set_record_status(record["id"], "entered")["status"], "entered")
        with self.assertRaises(ValueError):
            app.write_record({"title": "no key"})
        content = (self.domain / "hq/os/records/shows" / f"{record['id']}.md").read_text()
        self.assertIn('app: "shows"', content)

    def test_run_adds_records_writes_note_run_and_log(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        out = agentapps.run(self.domain, "shows", deps=DEPS)
        self.assertEqual(out["changes"], {"new": 2, "updated": 0, "total": 2})
        self.assertIsNone(out["error"])
        self.assertTrue(out["note"])
        notes = list((self.domain / "hq/os/inbox").glob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertIn("Shows: 2 new, 0 updated", notes[0].read_text())
        runs = list((self.domain / "hq/os/runs").rglob("*.json"))
        self.assertEqual(len(runs), 1)
        saved = json.loads(runs[0].read_text())
        self.assertEqual((saved["agent"], saved["usage"]), ("app:shows", {"input": 120, "output": 40}))
        system = FakeAgentRun.calls[0]["system"]
        self.assertIn(PROMPT, system)
        self.assertIn("Randy shoots thresholds", system)
        self.assertIn("No em dashes", system)
        self.assertIn("write_record", FakeAgentRun.calls[0]["tools"])
        # a second run with the same findings changes nothing and writes no note
        out2 = agentapps.run(self.domain, "shows", deps=DEPS)
        self.assertEqual(out2["changes"]["new"], 0)
        self.assertIsNone(out2["note"])
        self.assertEqual(len(list((self.domain / "hq/os/inbox").glob("*.md"))), 1)
        self.assertIn("Existing records (2)", FakeAgentRun.calls[1]["system"])

    def test_run_without_a_model_layer_records_the_failure(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        out = agentapps.run(self.domain, "shows", deps={"llm": None, "agentrun": None, "chats": None, "basetools": None})
        self.assertIn("aihq.llm", out["error"])
        self.assertEqual(out["changes"]["new"], 0)
        self.assertTrue(list((self.domain / "hq/os/runs").rglob("*.json")))

    def test_chat_with_an_app_keeps_a_thread(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        first = agentapps.chat(self.domain, "shows", None, "What is the nearest deadline?", deps=DEPS)
        self.assertTrue(first["thread"])
        second = agentapps.chat(self.domain, "shows", first["thread"], "And the fee?", deps=DEPS)
        self.assertEqual(second["thread"], first["thread"])
        self.assertEqual(len(FakeChats.store[first["thread"]]["messages"]), 4)
        self.assertEqual(len(FakeAgentRun.calls[1]["messages"]), 3)
        self.assertIn("conversation with", FakeAgentRun.calls[0]["system"])

    def test_adapter_manifest_snapshot_actions(self):
        app = agentapps.create(self.domain, SPEC, PROMPT)
        adapter = agentapps.AgentAppAdapter(app)
        manifest = adapter.manifest()
        self.assertEqual(manifest["kind"], "agent")
        self.assertEqual([v["id"] for v in manifest["views"]], ["board", "chat", "settings"])
        self.assertEqual(manifest["web"]["script"], "/agent-app.js")
        self.assertEqual(adapter.routines(), [{"id": "run", "name": "Shows run", "every": "1d"}])
        app.write_record({"title": "Salon", "url": "https://example.org/salon"})
        snap = adapter.snapshot()
        self.assertEqual(snap["counts"]["records"], 1)
        self.assertEqual(snap["key"], "url")
        rid = snap["records"][0]["id"]
        self.assertEqual(adapter.action({"action": "record.set", "id": rid, "fields": {"status": "considering"}})["record"]["status"], "considering")
        self.assertTrue(adapter.action({"action": "record.delete", "id": rid})["ok"])
        self.assertEqual(adapter.action({"action": "app.update", "spec": {"every": "12h"}})["spec"]["every"], "12h")
        with self.assertRaises(ValueError):
            adapter.action({"action": "nope"})
        self.assertEqual(adapter.agents()[0]["name"], "Shows")
        self.assertEqual(adapter.usage()["runs_30d"], 0)

    def test_app_ops_tools_create_an_app_from_a_tool_call(self):
        tools = agentapps.AppOpsTools(self.domain)
        self.assertIn("create_app", [s["name"] for s in tools.specs()])
        out = json.loads(tools.call("create_app", {**SPEC, "id": "fairs", "prompt": PROMPT}))
        self.assertTrue(out["ok"])
        self.assertIn("fairs", agentapps.load(self.domain))
        listed = json.loads(tools.call("list_apps", {}))
        self.assertEqual(listed[0]["id"], "fairs")
        json.loads(tools.call("delete_app", {"id": "fairs"}))
        self.assertEqual(agentapps.load(self.domain), {})


class AgentAppRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        self.domain = self.repo.parent
        (self.domain / "hq" / "os").mkdir(parents=True)
        agentapps.invalidate()
        modules.invalidate()
        FakeAgentRun.calls = []
        FakeChats.store = {}
        self.runtime = Runtime(root=self.repo, home=self.home)
        self.runtime.reconcile()

    def test_agent_app_shows_up_as_a_module_and_gets_a_schedule(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        self.runtime.reconcile()
        snapshot = self.runtime.snapshot()
        self.assertIn("shows", snapshot["modules"])
        self.assertEqual(snapshot["modules"]["shows"]["manifest"]["kind"], "agent")
        self.assertEqual(snapshot["modules"]["shows"]["snapshot"]["counts"]["records"], 0)
        self.assertIn("shows", {a["id"] for a in snapshot["apps"]})
        workflows = {s["workflow"]: s for s in snapshot["schedules"]}
        self.assertIn("module:shows:run", workflows)
        self.assertEqual(workflows["module:shows:run"]["interval_seconds"], 86400)
        self.assertTrue(any(a["id"] == "shows:agent" for a in snapshot["agents"]))

    def test_scheduled_run_goes_through_the_worker(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        self.runtime.reconcile()
        with patch.object(agentapps, "_deps", lambda deps=None: DEPS):
            self.runtime.action({"action": "task.create", "title": "Shows run", "operation": "module.routine",
                                 "payload": {"app": "shows", "routine": "run"}})
            processed = self.runtime.tick()["processed"]
        self.assertEqual(len(processed), 1)
        task = next(t for t in self.runtime.snapshot()["tasks"] if t["id"] == processed[0])
        self.assertEqual(task["status"], "succeeded", task.get("error"))
        self.assertEqual(len(agentapps.get(self.domain, "shows").records()), 2)

    def test_chat_send_with_app_and_apps_actions(self):
        with patch.object(agentapps, "_deps", lambda deps=None: DEPS):
            created = self.runtime.action({"action": "apps.create", "spec": SPEC, "prompt": PROMPT})
            self.assertEqual(created["id"], "shows")
            first = self.runtime.action({"action": "chat.send", "app": "shows", "text": "Anything new?"})
            self.assertEqual(first["agent"], "app:shows")
            self.assertIn("Verdict", first["message"]["text"])
            again = self.runtime.action({"action": "chat.send", "thread": first["thread"], "text": "Thanks"})
            self.assertEqual(again["thread"], first["thread"])  # thread ownership routes back to the app
            ran = self.runtime.action({"action": "apps.run", "id": "shows"})
            self.assertEqual(ran["changes"]["new"], 2)
            listed = self.runtime.action({"action": "apps.list"})["apps"]
            self.assertEqual(listed[0]["records"], 2)
            self.runtime.action({"action": "apps.delete", "id": "shows"})
        self.assertEqual(agentapps.load(self.domain), {})

    def test_operator_chat_can_create_an_app(self):
        class CreatingRun(FakeAgentRun):
            @staticmethod
            def run(model, system, messages, toolbelt, max_steps=16, on_step=None):
                result = toolbelt.call("create_app", {**SPEC, "id": "from-chat", "prompt": PROMPT})
                return {"answer": "Made it: " + result, "tool_calls": [{"name": "create_app"}], "usage": {"input": 1, "output": 1}}
        deps = {**DEPS, "agentrun": CreatingRun}
        with patch.object(agentapps, "_deps", lambda d=None: deps):
            out = self.runtime.action({"action": "chat.send", "agent": "operator", "text": "Create an app that finds art shows daily"})
        self.assertEqual(out["agent"], "operator")
        self.assertIn("from-chat", agentapps.load(self.domain))
        self.assertIn("module:from-chat:run", {s["workflow"] for s in self.runtime.snapshot()["schedules"]})


class AgentAppCliTests(unittest.TestCase):
    def test_apps_create_and_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            domain = Path(temporary)
            (domain / "hq" / "os").mkdir(parents=True)
            prompt_file = domain / "p.md"
            prompt_file.write_text(PROMPT, encoding="utf-8")
            agentapps.invalidate()
            fake_root = domain / "os"
            fake_root.mkdir()
            out = io.StringIO()
            with patch("aihq.brain.ROOT", fake_root), patch("sys.stdout", out):
                code = cli.main(["apps", "create", "fairs", "--name", "Fairs", "--every", "1d", "--fields", "title,url",
                                 "--web", "--prompt-file", str(prompt_file)])
                self.assertEqual(code, 0, out.getvalue())
                cli.main(["apps", "list"])
            text = out.getvalue()
            self.assertIn("created fairs: Fairs (1d)", text)
            self.assertIn("fairs", text.splitlines()[-1])
            self.assertTrue((domain / "hq/os/apps/fairs/prompt.md").is_file())


if __name__ == "__main__":
    unittest.main()
