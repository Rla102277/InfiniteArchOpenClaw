import io
import json
import os
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest import mock

from aihq import secrets
from aihq.builtins import calendar_app
from aihq.builtins.gmail import GmailConnector
from aihq.builtins.instagram import InstagramConnector
from aihq.builtins.site import SiteConnector
from aihq.builtins.telegram import TelegramConnector


class Env(unittest.TestCase):
    def setUp(self):
        self.domain = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(os.environ, {"AIHQ_HOME": tempfile.mkdtemp(), "CURATOR_SECRETS_BACKEND": "file"})
        self.env.start()

    def tearDown(self):
        self.env.stop()


def _message(subject="Call for entry", body="Deadline Dec 15", html=False):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Gallery <g@x.org>", "rla1022@gmail.com", subject, "<abc@x>"
    if html:
        m.set_content("<p>Hello <b>Randy</b></p><script>x()</script>", subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


class FakeIMAP:
    instances: list = []

    def __init__(self, host):
        self.host, self.appended, self.logged = host, [], None
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.logged = (user, password)

    def select(self, box, readonly=False):
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.query = args
            return "OK", [b"11 12"]
        if command == "FETCH":
            if "RFC822" in args[1]:
                return "OK", [(b"12 (RFC822 {})", _message(html=args[0] == "12"))]
            return "OK", [(b"h", b"From: Gallery <g@x.org>\r\nSubject: Call\r\nDate: Mon, 1 Dec 2026\r\n\r\n"), (b"t", b"Deadline <b>Dec 15</b>")]
        return "NO", []

    def append(self, box, flags, when, data):
        self.appended.append((box, flags, data))
        return "OK", [b""]

    def logout(self):
        pass


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port):
        self.host = host

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    def quit(self):
        pass


class GmailTests(Env):
    def connector(self):
        g = GmailConnector(self.domain, {"address": "rla1022@gmail.com"})
        g.imap_factory, g.smtp_factory = FakeIMAP, FakeSMTP
        return g

    def test_status_says_what_is_missing(self):
        status = GmailConnector(self.domain, {}).status()
        self.assertFalse(status["ok"])
        self.assertIn("2-step verification", status["detail"])
        self.assertEqual(set(status["needs"]), {"address", "gmail-app-password"})

    def test_search_read_draft_send(self):
        secrets.put("gmail-app-password", "abcd efgh ijkl mnop")
        g = self.connector()
        found = json.loads(g.call("search", {"query": "subject:call", "limit": 5}))
        self.assertEqual([m["uid"] for m in found], ["12", "11"])
        self.assertEqual(found[0]["subject"], "Call")
        self.assertIn("Deadline Dec 15", found[0]["snippet"])
        self.assertEqual(FakeIMAP.instances[-1].logged, ("rla1022@gmail.com", "abcdefghijklmnop"))
        read = json.loads(g.call("read", {"uid": "12"}))
        self.assertIn("Hello Randy", read["body"])
        self.assertNotIn("x()", read["body"])
        self.assertIn("Draft saved", g.call("draft", {"to": "g@x.org", "subject": "Re: Call", "body": "Yes \u2014 I will enter", "reply_to_uid": "11"}))
        box, flags, data = FakeIMAP.instances[-1].appended[0]
        self.assertEqual(box, '"[Gmail]/Drafts"')
        self.assertIn(b"In-Reply-To: <abc@x>", data)
        self.assertNotIn("\u2014".encode(), data)
        self.assertIn("Sent to", g.call("send", {"to": "g@x.org", "subject": "Hi", "body": "b"}))
        self.assertEqual(FakeSMTP.sent[-1]["To"], "g@x.org")


class CalendarTests(Env):
    def test_applescript_builds_dates_from_components(self):
        start, _ = calendar_app._parse("2026-12-15T18:30")
        script = calendar_app.create_script('TPS "Members" Show', start, start, False, "Infinite Arch", url="https://x")
        for line in ("set year of startDate to 2026", "set month of startDate to 12", "set day of startDate to 15",
                     "set hours of startDate to 18", "set minutes of startDate to 30", 'summary:"TPS \\"Members\\" Show"',
                     'return "exists"', 'url:"https://x"'):
            self.assertIn(line, script)
        self.assertNotIn('date "', script)

    def test_ics_fallback_off_the_mac_and_all_day(self):
        cal = calendar_app.CalendarConnector(self.domain, {})
        with mock.patch.object(calendar_app.sys, "platform", "linux"):
            out = cal.call("create_event", {"title": "Life Framer \u2014 Travel: entries close", "start": "2026-10-31", "url": "https://lf"})
        self.assertIn("hq/os/calendar/", out)
        path = next((self.domain / "hq" / "os" / "calendar").glob("*.ics"))
        text = path.read_text()
        self.assertIn("DTSTART;VALUE=DATE:20261031", text)
        self.assertIn("DTEND;VALUE=DATE:20261101", text)
        self.assertIn("SUMMARY:Life Framer \\, Travel: entries close", text)
        self.assertIn("UID:", text)

    def test_created_and_exists_on_the_mac(self):
        cal = calendar_app.CalendarConnector(self.domain, {"name": "IA"})

        class R:
            def __init__(self, out):
                self.returncode, self.stdout = 0, out

        with mock.patch.object(calendar_app.sys, "platform", "darwin"):
            cal.runner = lambda script: R("created\n")
            self.assertIn("Added to IA", cal.call("create_event", {"title": "Show", "start": "2026-12-15T09:00"}))
            cal.runner = lambda script: R("exists")
            self.assertIn("Already on IA", cal.call("create_event", {"title": "Show", "start": "2026-12-15"}))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TelegramTests(Env):
    def test_status_steps(self):
        self.assertIn("BotFather", TelegramConnector(self.domain, {}).status()["detail"])
        secrets.put("telegram-bot-token", "123:abc")
        self.assertIn("/start", TelegramConnector(self.domain, {}).status()["detail"])
        self.assertTrue(TelegramConnector(self.domain, {"chat_id": "9"}).status()["ok"])

    def test_send_chunks_and_photo_multipart(self):
        secrets.put("telegram-bot-token", "123:abc")
        seen = []

        def opener(request, timeout=0):
            seen.append(request)
            return FakeResponse(json.dumps({"ok": True, "result": {"message_id": 1}}).encode())

        t = TelegramConnector(self.domain, {"chat_id": "9"})
        t.opener = opener
        self.assertIn("Sent", t.call("send", {"text": "x" * 4500}))
        self.assertEqual(len(seen), 2)
        self.assertIn("/bot123:abc/sendMessage", seen[0].full_url)
        photo = self.domain / "hq" / "os" / "uploads" / "a.jpg"
        photo.parent.mkdir(parents=True)
        photo.write_bytes(b"\xff\xd8jpeg")
        self.assertIn("Photo sent", t.call("send_photo", {"path": "hq/os/uploads/a.jpg", "caption": "Where the Light Was Left On"}))
        self.assertIn(b"filename=\"a.jpg\"", seen[-1].data)
        self.assertIn("multipart/form-data", seen[-1].headers["Content-type"])
        self.assertIn("must be a file under hq/", t.call("send_photo", {"path": "../etc/passwd"}))

    def test_api_error_is_readable(self):
        secrets.put("telegram-bot-token", "123:abc")
        t = TelegramConnector(self.domain, {"chat_id": "9"})
        t.opener = lambda request, timeout=0: FakeResponse(json.dumps({"ok": False, "description": "Unauthorized"}).encode())
        with self.assertRaises(ValueError) as caught:
            t.api("getMe")
        self.assertIn("Unauthorized", str(caught.exception))
        self.assertNotIn("123:abc", str(caught.exception))


class DraftTests(Env):
    def test_instagram_and_site_drafts(self):
        out = InstagramConnector(self.domain, {}).call("draft_post", {"caption": "Where the Road Learned to Pray \u2014 Kyoto", "image_path": ""})
        self.assertIn("Posts board", out)
        record = next((self.domain / "hq" / "os" / "records" / "posts").glob("*.md")).read_text()
        self.assertIn('status: "draft"', record)
        self.assertNotIn("\u2014", record)
        self.assertIn("not wired yet", InstagramConnector(self.domain, {}).call("publish", {"id": "x"}))
        self.assertIn("hq/os/drafts/site/", SiteConnector(self.domain, {}).call("draft_update", {"title": "New work", "body": "Three frames."}))


if __name__ == "__main__":
    unittest.main()
