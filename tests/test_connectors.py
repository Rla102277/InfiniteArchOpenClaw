import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aihq import activity, agentapps, connectors, core, mcp, secrets
from aihq.builtins.base import Connector, spec

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


class FakeMail(Connector):
    id = "gmail"
    name = "Gmail"
    ACTIONS = {"send"}
    sent: list = []

    def tools(self):
        return [spec("search", "s", {"query": {"type": "string"}}), spec("send", "s", {"to": {"type": "string"}, "subject": {"type": "string"}})]

    def tool_search(self, query=""):
        return "2 messages"

    def tool_send(self, to="", subject=""):
        FakeMail.sent.append((to, subject))
        return f"Sent to {to}"


class Base(unittest.TestCase):
    def setUp(self):
        self.domain = Path(tempfile.mkdtemp())
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"AIHQ_HOME": self.home, "CURATOR_SECRETS_BACKEND": "file"})
        self.env.start()
        FakeMail.sent = []
        self.builtins = mock.patch.dict(connectors.BUILTINS, {"gmail": FakeMail})
        self.builtins.start()

    def tearDown(self):
        self.builtins.stop()
        self.env.stop()
        mcp.close_all()


class ConfigTests(Base):
    def test_missing_config_means_builtins_with_reasons(self):
        rows = {r["id"]: r for r in connectors.describe(self.domain)}
        self.assertIn("calendar", rows)
        self.assertIn("telegram", rows)
        self.assertFalse(rows["telegram"]["status"]["ok"])
        self.assertIn("BotFather", rows["telegram"]["status"]["detail"])

    def test_add_update_remove_mcp_and_secret_guard(self):
        connectors.add_mcp(self.domain, {"id": "fake", "command": sys.executable, "args": [FAKE], "env": {"FAKE_TOKEN": "secret:mcp-fake-FAKE_TOKEN"}})
        with self.assertRaises(ValueError):
            connectors.add_mcp(self.domain, {"id": "leaky", "command": "x", "env": {"API_KEY": "sk-plain"}})
        status = connectors.registry(self.domain)["mcp.fake"].status()
        self.assertFalse(status["ok"])
        self.assertIn("mcp-fake-FAKE_TOKEN", status["needs"])
        secrets.put("mcp-fake-FAKE_TOKEN", "t")
        self.assertTrue(connectors.registry(self.domain)["mcp.fake"].status()["ok"])
        connectors.update(self.domain, "mcp.fake", {"name": "Fake server"})
        connectors.update(self.domain, "telegram", {"chat_id": "42", "allow": [7]})
        data = json.loads((self.domain / "hq" / "os" / "connectors.json").read_text())
        self.assertEqual(data["mcp"][0]["name"], "Fake server")
        self.assertEqual(data["builtin"]["telegram"]["allow"], [7])
        self.assertNotIn("sk-plain", json.dumps(data))
        self.assertTrue(connectors.remove_mcp(self.domain, "fake"))
        self.assertNotIn("mcp.fake", connectors.registry(self.domain))


class PolicyTests(Base):
    def test_policy_lookup_and_always_ask(self):
        policy = {"gmail.send": "auto", "calendar.*": "auto", "*": "ask"}
        self.assertEqual(connectors.policy_for(policy, "gmail.send"), "auto")
        self.assertEqual(connectors.policy_for(policy, "calendar.create_event"), "auto")
        self.assertEqual(connectors.policy_for(policy, "telegram.send"), "ask")
        self.assertEqual(connectors.policy_for({}, "telegram.send"), "ask")
        self.assertEqual(connectors.policy_for({"*": "auto"}, "darkroom.print_submit"), "ask")
        self.assertEqual(connectors.policy_for({"*": "auto"}, "mcp.x.photos_push"), "ask")

    def test_reads_run_auto_actions_run_and_record(self):
        connectors.update(self.domain, "gmail", {"address": "a@b"})
        belt = connectors.ConnectorTools(self.domain, ["gmail"], {"gmail.send": "auto"}, who="app:show-scout")
        names = [s["name"] for s in belt.specs()]
        self.assertEqual(names, ["gmail__search", "gmail__send"])
        self.assertIn("[acts now]", belt.specs()[1]["description"])
        self.assertEqual(belt.call("gmail__search", {"query": "x"}), "2 messages")
        self.assertEqual(belt.call("gmail.send", {"to": "j@x", "subject": "Entry"}), "Sent to j@x")
        self.assertEqual(FakeMail.sent, [("j@x", "Entry")])
        rows = activity.recent(self.domain)
        self.assertEqual([(r["tool"], r["status"]) for r in rows], [("send", "done")])

    def test_ask_queues_an_approval_and_execute_runs_once(self):
        belt = connectors.ConnectorTools(self.domain, "all", {"*": "ask"}, who="app:show-scout")
        out = belt.call("gmail__send", {"to": "j@x", "subject": "Entry"})
        self.assertTrue(out.startswith("QUEUED"), out)
        self.assertEqual(FakeMail.sent, [])
        item_id = belt.calls[0]["id"]
        path = self.domain / "hq" / "os" / "inbox" / f"{item_id}.md"
        meta, body = connectors._frontmatter(path.read_text())
        for key in ("id", "kind", "title", "status", "created", "decided", "note", "agent", "action"):
            self.assertIn(key, meta)
        self.assertEqual((meta["kind"], meta["action"], meta["status"]), ("approval", "connector.call", "open"))
        result = connectors.execute_approved(self.domain, item_id)
        self.assertTrue(result["ok"])
        self.assertEqual(FakeMail.sent, [("j@x", "Entry")])
        meta, _ = connectors._frontmatter(path.read_text())
        self.assertEqual(meta["status"], "approved")
        self.assertIn("Sent to j@x", meta["note"])
        with self.assertRaises(ValueError):
            connectors.execute_approved(self.domain, item_id)
        self.assertEqual(len(FakeMail.sent), 1)
        self.assertEqual([r["status"] for r in activity.recent(self.domain)], ["done", "queued"])

    def test_mcp_server_tools_through_the_belt(self):
        connectors.add_mcp(self.domain, {"id": "fake", "command": sys.executable, "args": [FAKE]})
        belt = connectors.ConnectorTools(self.domain, ["mcp.fake"], {"mcp.fake.post": "auto"}, who="operator")
        self.assertEqual([s["name"] for s in belt.specs()], ["mcp__fake__lookup", "mcp__fake__post"])
        self.assertEqual(belt.call("mcp__fake__lookup", {"q": "a"}), 'lookup:{"q": "a"}')
        self.assertEqual(belt.call("mcp.fake.post", {"text": "x"}), 'post:{"text": "x"}')
        self.assertEqual(activity.recent(self.domain)[0]["connector"], "mcp.fake")
        ask = connectors.ConnectorTools(self.domain, ["mcp.fake"], {}, who="operator")
        self.assertTrue(ask.call("mcp__fake__post", {"text": "y"}).startswith("QUEUED"))

    def test_a_broken_server_does_not_take_the_belt_down(self):
        connectors.add_mcp(self.domain, {"id": "dead", "command": "/no/such/server"})
        connectors.update(self.domain, "gmail", {"address": "a@b"})
        belt = connectors.ConnectorTools(self.domain, "all", {}, who="operator")
        names = [s["name"] for s in belt.specs()]
        self.assertIn("gmail__search", names)
        self.assertFalse(any(n.startswith("mcp__dead") for n in names))


if __name__ == "__main__":
    unittest.main()


class AlwaysAskTermsTests(unittest.TestCase):
    def tearDown(self):
        core.local_config.cache_clear()

    def _config(self, terms):
        def fake():
            return {"always_ask_terms": terms} if terms else {}
        return mock.patch.object(core, "local_config", side_effect=fake)

    def test_config_terms_always_ask_even_when_policy_is_auto(self):
        with self._config(["darkroom"]):
            self.assertEqual(connectors.policy_for({"*": "auto"}, "mcp.darkroom.cmd_curator_apply"), "ask")
            self.assertEqual(agentapps.policy_for({"*": "auto"}, "mcp.darkroom.cmd_curator_apply"), "ask")

    def test_without_config_tool_follows_policy_and_builtins_still_ask(self):
        with self._config([]):
            self.assertEqual(connectors.policy_for({"*": "auto"}, "mcp.darkroom.cmd_curator_apply"), "auto")
            self.assertEqual(agentapps.policy_for({"*": "auto"}, "mcp.darkroom.cmd_curator_apply"), "auto")
            self.assertEqual(connectors.policy_for({"*": "auto"}, "photos.write"), "ask")
            self.assertEqual(agentapps.policy_for({"*": "auto"}, "print.send"), "ask")
