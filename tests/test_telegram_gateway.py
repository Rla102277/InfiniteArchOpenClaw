"""The Telegram gateway against a fake Bot API: allowlist, chat, photos, commands, approvals, notify."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq import agentapps, telegram_gateway as tg


class FakeAPI:
    """A function table keyed by Bot API method; every call is recorded."""

    def __init__(self, updates=None):
        self.updates = list(updates or [])
        self.calls = []

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        if method == "getFile":
            return {"file_path": "photos/file_1.jpg"}
        return {"message_id": len(self.calls)}

    def download(self, path):
        return b"\xff\xd8\xff jpeg bytes"

    def sent(self, method="sendMessage"):
        return [p for m, p in self.calls if m == method]


class FakeRuntime:
    def __init__(self):
        self.actions = []

    def action(self, payload):
        self.actions.append(payload)
        if payload["action"] == "chat.send":
            return {"thread": "20261029-telegram", "message": {"text": f"Answer to: {payload['text']}"}}
        if payload["action"] == "inbox.set":
            return {"ok": True, "message": "Done: event created"}
        if payload["action"] == "apps.run":
            return {"app": payload["id"], "error": None, "changes": {"new": 2, "updated": 0},
                    "answer": "Two new shows.", "tool_calls": []}
        return {}


def msg(update_id, user, text=None, photo=None, chat=None):
    message = {"message_id": update_id, "from": {"id": user}, "chat": {"id": chat or user}}
    if text is not None:
        message["text"] = text
    if photo:
        message["photo"] = photo
    return {"update_id": update_id, "message": message}


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.domain = Path(self.tmp.name) / "domain"
        self.private = Path(self.tmp.name) / "private"
        (self.domain / "hq" / "os").mkdir(parents=True)
        tg.save_config(self.domain, {"allow": ["111"]})
        self.runtime = FakeRuntime()
        agentapps.invalidate()

    def gateway(self, api):
        return tg.Gateway(self.runtime, domain=self.domain, api=api, private_dir=self.private)

    def test_strangers_get_their_id_on_start_and_nothing_else(self):
        api = FakeAPI([msg(1, 999, "/start"), msg(2, 999, "show me the inbox")])
        self.gateway(api).poll_once()
        self.assertEqual(len(api.sent()), 1)
        self.assertIn("999", api.sent()[0]["text"])
        self.assertEqual(self.runtime.actions, [], "an unknown user never reaches the OS")
        self.assertIsNone(tg.config(self.domain).get("chat_id"))

    def test_text_goes_to_the_operator_and_the_answer_comes_back(self):
        api = FakeAPI([msg(5, 111, "What is due this week?")])
        gateway = self.gateway(api)
        gateway.poll_once()
        chat = [a for a in self.runtime.actions if a["action"] == "chat.send"]
        self.assertEqual(chat[0]["agent"], "operator")
        self.assertEqual(chat[0]["text"], "What is due this week?")
        self.assertEqual(api.sent()[-1]["text"], "Answer to: What is due this week?")
        self.assertEqual(tg.config(self.domain)["chat_id"], "111", "the chat is learned from the owner's first message")
        # the offset survives a restart; the day's thread is reused
        again = FakeAPI([msg(6, 111, "and next week?")])
        second = self.gateway(again)
        self.assertEqual(second.state["offset"], 6)
        second.poll_once()
        self.assertEqual(again.calls[0][1]["offset"], 6)
        self.assertEqual(self.runtime.actions[-1].get("thread"), "20261029-telegram")

    def test_long_answers_are_split(self):
        parts = tg.split("x" * 9000)
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= tg.MAX_TEXT for p in parts))

    def test_a_photo_goes_to_the_critic(self):
        api = FakeAPI([msg(7, 111, photo=[{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 900}])])
        api.updates[0]["message"]["caption"] = "grade this"
        self.gateway(api).poll_once()
        self.assertEqual(api.sent("getFile")[0]["file_id"], "big")
        chat = [a for a in self.runtime.actions if a["action"] == "chat.send"][0]
        self.assertEqual((chat["agent"], chat["text"]), ("critic", "grade this"))
        self.assertEqual(chat["images"][0]["mime"], "image/jpeg")
        self.assertTrue(chat["images"][0]["data_base64"])

    def test_bot_command_runs_the_bot(self):
        agentapps.create(self.domain, {"id": "art-shows", "name": "Show Scout", "fields": ["url"],
                                       "triggers": [{"type": "telegram", "command": "/scout"}]},
                         "Find juried photography shows and calls for entry; record the official url.")
        api = FakeAPI([msg(8, 111, "/scout")])
        self.gateway(api).poll_once()
        self.assertIn({"action": "apps.run", "id": "art-shows"}, self.runtime.actions)
        texts = [p["text"] for p in api.sent()]
        self.assertTrue(any("2 new" in t for t in texts), texts)
        unknown = FakeAPI([msg(9, 111, "/nonsense")])
        self.gateway(unknown).poll_once()
        self.assertIn("/scout", unknown.sent()[-1]["text"])

    def test_today_and_inbox(self):
        (self.domain / "hq" / "HQ.md").write_text("# HQ\n\n## Top 3\n\n- **Frame Trolls**\n- Book the framer\n\n## Due now\n", encoding="utf-8")
        agentapps.add_inbox(self.domain, "note", "Show Scout: 2 new", "body")
        api = FakeAPI([msg(10, 111, "/today"), msg(11, 111, "/inbox")])
        self.gateway(api).poll_once()
        texts = [p["text"] for p in api.sent()]
        self.assertTrue(texts[0].startswith("Today, top 3:") and "Frame Trolls" in texts[0] and "**" not in texts[0])
        self.assertIn("Show Scout: 2 new", texts[1])

    def test_approvals_are_announced_once_and_a_tap_decides(self):
        tg.save_config(self.domain, {"chat_id": "111"})
        item = agentapps.add_inbox(self.domain, "approval", "Send: reply to the juror", "gmail.send to a@b.c", action="connector.call")
        api = FakeAPI()
        gateway = self.gateway(api)
        gateway.poll_once()
        gateway.poll_once()
        announced = [p for p in api.sent() if p.get("reply_markup")]
        self.assertEqual(len(announced), 1, "announced once")
        buttons = announced[0]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["callback_data"] for b in buttons], [f"ap:{item['id']}", f"rj:{item['id']}"])
        tap = {"update_id": 20, "callback_query": {"id": "cb1", "from": {"id": 111}, "data": f"ap:{item['id']}",
                                                     "message": {"message_id": 3, "chat": {"id": 111}, "text": "Needs your OK"}}}
        api.updates = [tap]
        gateway.poll_once()
        self.assertIn({"action": "inbox.set", "id": item["id"], "status": "approved", "note": "Decided from Telegram."}, self.runtime.actions)
        edited = api.sent("editMessageText")[0]
        self.assertIn("Approved", edited["text"])
        # a stranger's tap does nothing
        api.updates = [{"update_id": 21, "callback_query": {"id": "cb2", "from": {"id": 5}, "data": f"rj:{item['id']}", "message": {}}}]
        before = len(self.runtime.actions)
        gateway.poll_once()
        self.assertEqual(len(self.runtime.actions), before)

    def test_notify(self):
        api = FakeAPI()
        self.assertFalse(tg.notify("hi", domain=self.domain, api=api), "no chat yet: quietly false")
        tg.save_config(self.domain, {"chat_id": "111"})
        self.assertTrue(tg.notify("Show Scout: 2 new", domain=self.domain, api=api))
        self.assertEqual(api.sent()[0], {"chat_id": "111", "text": "Show Scout: 2 new", "disable_web_page_preview": True})
        with patch.object(tg, "token", lambda: None):
            self.assertFalse(tg.notify("x", domain=self.domain))

    def test_a_bad_update_never_stops_the_loop(self):
        class Broken(FakeRuntime):
            def action(self, payload):
                raise RuntimeError("boom")
        self.runtime = Broken()
        logs = []
        api = FakeAPI([msg(30, 111, "hello"), msg(31, 111, "/start")])
        gateway = tg.Gateway(self.runtime, domain=self.domain, api=api, private_dir=self.private, log=logs.append)
        self.assertEqual(gateway.poll_once(), 2)
        self.assertTrue(any("boom" in line for line in logs))
        self.assertIn("Connected", api.sent()[-1]["text"])
        self.assertEqual(gateway.state["offset"], 32)


if __name__ == "__main__":
    unittest.main()
