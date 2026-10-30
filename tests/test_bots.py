"""Bots: triggers, connectors and the per-action policy, model refs, email polling, Telegram commands,
the notify path, and approvals of queued connector calls through the OS inbox."""
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq import agentapps

PROMPT = "Find juried photography shows. Record the official url. Put real deadlines on the calendar and text Randy the best two."
SPEC = {"id": "scout", "name": "Scout", "description": "test bot", "fields": ["title", "deadline", "url"], "key": "url",
        "triggers": [{"type": "schedule", "at": "07:00"}, {"type": "telegram", "command": "/scout"},
                     {"type": "email", "query": "from:submittable newer_than:2d", "every": "30m"}],
        "connectors": ["calendar", "telegram", "gmail"],
        "policy": {"calendar.create_event": "auto", "telegram.send": "auto", "*": "ask"}}


class FakeConnectorTools:
    """Stands in for aihq.connectors.ConnectorTools: records every call and honors the policy."""
    instances = []
    inbox = []
    mail = []

    def __init__(self, domain, allowed, policy, who, runtime=None):
        self.domain, self.allowed, self.policy, self.who = domain, allowed, policy, who
        self.calls = []
        FakeConnectorTools.instances.append(self)

    def specs(self):
        return [{"name": n, "input_schema": {"type": "object"}} for n in
                ("calendar.create_event", "telegram.send", "gmail.search", "gmail.send")]

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "gmail.search":
            return json.dumps({"messages": FakeConnectorTools.mail})
        if agentapps.policy_for(self.policy, name) == "ask":
            FakeConnectorTools.inbox.append((name, args))
            return "QUEUED for Randy's approval: x1"
        return json.dumps({"ok": True, "tool": name})


FakeConnectors = types.SimpleNamespace(ConnectorTools=FakeConnectorTools)


class FakeModel:
    name = "stub"
    web = False
    supports_web = False


class FakeLLM:
    resolved = []

    @staticmethod
    def pick(kind, web=False):
        return FakeModel()

    @staticmethod
    def resolve(ref):
        FakeLLM.resolved.append(ref)
        return FakeModel()


class FakeAgentRun:
    """The loop: makes one record, puts it on the calendar, texts Randy, tries to email (queued)."""
    calls = []

    @staticmethod
    def run(model, system, messages, toolbelt, max_steps=16, web=False):
        FakeAgentRun.calls.append({"system": system, "prompt": messages[-1]["content"], "tools": [s["name"] for s in toolbelt.specs()]})
        tool_calls = []
        for name, args in (("write_record", {"title": "TPS Members", "deadline": "2026-12-15", "url": "https://ex.org/tps"}),
                           ("calendar.create_event", {"title": "Deadline: TPS Members", "start": "2026-12-15", "all_day": True}),
                           ("telegram.send", {"text": "Enter TPS Members by Dec 15."}),
                           ("gmail.send", {"to": "a@b.c", "subject": "x", "body": "y"})):
            tool_calls.append({"name": name, "args": args, "result": toolbelt.call(name, args)})
        return {"answer": "Verdict: one new show, on the calendar, texted.", "tool_calls": tool_calls,
                "usage": {"input": 10, "output": 5}, "stop": "end_turn"}


class FakeGateway:
    sent = []

    @staticmethod
    def notify(text, domain=None):
        FakeGateway.sent.append(text)
        return True


DEPS = {"llm": FakeLLM, "agentrun": FakeAgentRun, "chats": None, "basetools": None, "connectors": FakeConnectors,
        "telegram_gateway": FakeGateway}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.domain = Path(self.tmp.name)
        (self.domain / "hq" / "os").mkdir(parents=True)
        FakeConnectorTools.instances, FakeConnectorTools.inbox, FakeConnectorTools.mail = [], [], []
        FakeAgentRun.calls, FakeGateway.sent, FakeLLM.resolved = [], [], []
        agentapps.invalidate()

    def test_validate_triggers_connectors_policy_and_model_ref(self):
        spec = agentapps.validate({**SPEC, "model": "openai:gpt-5"})
        self.assertEqual(spec["model"], "openai:gpt-5")
        self.assertEqual([t["type"] for t in spec["triggers"]], ["schedule", "telegram", "email"])
        self.assertEqual(spec["connectors"], ["calendar", "telegram", "gmail"])
        forced = agentapps.validate({**SPEC, "policy": {"print.submit": "auto", "darkroom.photos_push": "auto", "*": "auto"}})
        self.assertEqual(forced["policy"]["print.submit"], "ask", "paper always asks")
        self.assertEqual(forced["policy"]["darkroom.photos_push"], "ask")
        for bad in ({**SPEC, "triggers": [{"type": "cron"}]}, {**SPEC, "triggers": [{"type": "telegram", "command": "/start"}]},
                    {**SPEC, "triggers": [{"type": "email"}]}, {**SPEC, "triggers": [{"type": "schedule"}]},
                    {**SPEC, "policy": {"calendar.create_event": "maybe"}}, {**SPEC, "connectors": "some"},
                    {**SPEC, "model": "openai"}, {**SPEC, "model": "acme:x"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                agentapps.validate(bad)
        old = agentapps.validate({"id": "old", "name": "Old", "fields": ["url"], "every": "1d"})
        self.assertEqual((old["triggers"], old["connectors"], old["policy"]), ([], [], {"*": "ask"}))

    def test_policy_lookup(self):
        policy = {"calendar.create_event": "auto", "telegram.*": "auto", "*": "ask"}
        self.assertEqual(agentapps.policy_for(policy, "calendar.create_event"), "auto")
        self.assertEqual(agentapps.policy_for(policy, "telegram.send_photo"), "auto")
        self.assertEqual(agentapps.policy_for(policy, "gmail.send"), "ask")
        self.assertEqual(agentapps.policy_for({"*": "auto"}, "print.submit"), "ask")
        self.assertEqual(agentapps.policy_for({}, "anything.at_all"), "ask")

    def test_routines_and_commands_come_from_triggers(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        app = agentapps.get(self.domain, "scout")
        self.assertEqual(app.routines(), [{"id": "run", "name": "Scout run", "at": "07:00"},
                                          {"id": "email-0", "name": "Scout email check", "every": "30m"}])
        self.assertEqual(agentapps.commands(self.domain), {"/scout": "scout"})
        old = agentapps.create(self.domain, {"id": "old", "name": "Old", "fields": ["url"], "every": "6h"}, PROMPT)
        self.assertEqual(old.routines(), [{"id": "run", "name": "Old run", "every": "6h"}])

    def test_run_uses_connectors_with_policy_and_resolves_the_model(self):
        agentapps.create(self.domain, {**SPEC, "model": "openai:gpt-5"}, PROMPT)
        result = agentapps.run(self.domain, "scout", deps=DEPS)
        self.assertIsNone(result["error"])
        self.assertEqual(FakeLLM.resolved, ["openai:gpt-5"])
        belt = FakeConnectorTools.instances[-1]
        self.assertEqual((belt.allowed, belt.who), (["calendar", "telegram", "gmail"], "app:scout"))
        self.assertIn("calendar.create_event", FakeAgentRun.calls[0]["tools"])
        self.assertIn("Connectors: calendar, telegram, gmail", FakeAgentRun.calls[0]["system"])
        self.assertEqual(FakeConnectorTools.inbox, [("gmail.send", {"to": "a@b.c", "subject": "x", "body": "y"})], "only the ask action queued")
        self.assertEqual(result["changes"]["new"], 1)
        self.assertEqual(FakeGateway.sent, [], "the bot texted Randy itself, so no second message")

    def test_notify_goes_to_telegram_when_the_bot_did_not_message_itself(self):
        class Quiet(FakeAgentRun):
            @staticmethod
            def run(model, system, messages, toolbelt, max_steps=16, web=False):
                toolbelt.call("write_record", {"title": "A", "url": "https://ex.org/a"})
                return {"answer": "One new.", "tool_calls": [{"name": "write_record"}], "usage": {}, "stop": "end_turn"}
        agentapps.create(self.domain, SPEC, PROMPT)
        agentapps.run(self.domain, "scout", deps={**DEPS, "agentrun": Quiet})
        self.assertEqual(len(FakeGateway.sent), 1)
        self.assertIn("Scout: 1 new", FakeGateway.sent[0])
        agentapps.create(self.domain, {**SPEC, "id": "silent", "connectors": ["calendar"]}, PROMPT)
        agentapps.run(self.domain, "silent", deps={**DEPS, "agentrun": Quiet})
        self.assertEqual(len(FakeGateway.sent), 1, "no telegram connector, no message")

    def test_email_trigger_runs_only_on_new_mail_and_is_quiet_otherwise(self):
        agentapps.create(self.domain, SPEC, PROMPT)
        runs = self.domain / "hq" / "os" / "runs"
        FakeConnectorTools.mail = []
        quiet = agentapps.check_email(self.domain, "scout", 0, deps=DEPS)
        self.assertEqual(quiet, {"app": "scout", "new_messages": 0})
        self.assertFalse(runs.exists() and any(runs.rglob("*.json")), "nothing noisy when there is no mail")
        FakeConnectorTools.mail = [{"uid": "41", "from": "calls@submittable.com", "subject": "New call: Salon", "date": "today", "snippet": "Enter by Dec 1"}]
        first = agentapps.check_email(self.domain, "scout", 0, deps=DEPS)
        self.assertEqual(first["new_messages"], 1)
        self.assertIn("New call: Salon", FakeAgentRun.calls[-1]["prompt"])
        count = len(FakeAgentRun.calls)
        again = agentapps.check_email(self.domain, "scout", 0, deps=DEPS)
        self.assertEqual(again["new_messages"], 0)
        self.assertEqual(len(FakeAgentRun.calls), count, "the same uid never runs the bot twice")
        state = json.loads((self.domain / "hq/os/apps/scout/state.json").read_text())
        self.assertEqual(state["email_seen"]["0"], ["41"])
        adapter = agentapps.AgentAppAdapter(agentapps.get(self.domain, "scout"))
        with patch.object(agentapps, "check_email", lambda d, a, i, runtime=None: {"called": i}):
            self.assertEqual(adapter.run_routine("email-0"), {"called": 0})
        with self.assertRaises(ValueError):
            adapter.run_routine("nope")


class InboxDecideTests(unittest.TestCase):
    """A queued connector call approved through the OS inbox runs once via execute_approved."""

    def test_connector_call_approval_runs_once(self):
        from aihq import runtime as runtime_module
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        domain = Path(tmp.name)
        item = agentapps.add_inbox(domain, "approval", "Send: reply to the juror", "gmail.send to a@b.c",
                                   agent="app:scout", action="connector.call", tool="gmail.send", args=json.dumps({"to": "a@b.c"}))
        executed = []
        fake_conn = types.SimpleNamespace(execute_approved=lambda d, it: executed.append(it["id"]) or {"summary": "sent"})
        fake = types.SimpleNamespace(brain=types.SimpleNamespace(root=domain / "os"), connect=None)

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        fake.connect = lambda: Conn()
        fake.event = lambda c, e, m: None
        with patch.dict("sys.modules", {"aihq.connectors": fake_conn}):
            import aihq
            with patch.object(aihq, "connectors", fake_conn, create=True):
                out = runtime_module.Runtime.inbox_decide(fake, item["id"], "approved")
                again = runtime_module.Runtime.inbox_decide(fake, item["id"], "approved")
        self.assertEqual(executed, [item["id"]])
        self.assertIn("sent", out["message"])
        self.assertIn("already done", again["message"])
        meta, _ = agentapps.frontmatter((domain / f"hq/os/inbox/{item['id']}.md").read_text())
        self.assertEqual(meta["status"], "done")


if __name__ == "__main__":
    unittest.main()
