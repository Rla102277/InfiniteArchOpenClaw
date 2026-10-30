import base64
import tempfile
import unittest
from pathlib import Path

from aihq import chats

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.domain = Path(self.tmp.name)
        (self.domain / "hq" / "os").mkdir(parents=True)
        self.chats = chats.Chats(self.domain)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_append_history_with_image(self):
        thread = self.chats.create("New thread", "critic")
        self.assertTrue(thread["id"].endswith("new-thread"))
        self.chats.append(thread["id"], "user", "Grade this frame", images=[{"data": PNG, "mime": "image/png", "name": "a.png"}])
        self.chats.append(thread["id"], "assistant", "Verdict: B+. Flat light.", tool_calls=[{"name": "boards", "args": {"board": "works"}}],
                          usage={"input": 100, "output": 20})
        got = self.chats.get(thread["id"])
        self.assertEqual(got["title"], "Grade this frame", "title comes from the first user message")
        self.assertEqual(got["messages"], 2)
        self.assertEqual([t["role"] for t in got["turns"]], ["user", "assistant"])
        self.assertEqual(got["turns"][1]["text"], "Verdict: B+. Flat light.", "tool details are stripped from text")
        self.assertEqual(len(got["turns"][0]["images"]), 1)
        uploads = list((self.domain / "hq" / "os" / "uploads").glob("*.png"))
        self.assertEqual(len(uploads), 1)
        history = self.chats.history(thread["id"])
        self.assertEqual(history[0]["role"], "user")
        kinds = [b["type"] for b in history[0]["content"]]
        self.assertEqual(kinds, ["image", "text"])
        self.assertEqual(history[0]["content"][0]["source"]["media_type"], "image/png")
        self.assertEqual(history[1]["content"][0]["text"], "Verdict: B+. Flat light.")
        # same image again is stored once
        self.chats.append(thread["id"], "user", "again", images=[{"data": PNG, "mime": "image/png"}])
        self.assertEqual(len(list((self.domain / "hq" / "os" / "uploads").glob("*.png"))), 1)
        listed = self.chats.list()
        self.assertEqual(listed[0]["id"], thread["id"])
        self.assertEqual(self.chats.list(app="art-shows"), [])

    def test_rejects_bad_images(self):
        thread = self.chats.create("t", "operator")
        with self.assertRaises(ValueError):
            self.chats.append(thread["id"], "user", "x", images=[{"data": b"gif", "mime": "image/gif"}])
        with self.assertRaises(ValueError):
            self.chats.append(thread["id"], "user", "x", images=[{"data": b"0" * (5 * 1024 * 1024 + 1), "mime": "image/jpeg"}])

    def test_budget_summary_and_image_cap(self):
        thread = self.chats.create("long", "operator")
        for i in range(12):
            self.chats.append(thread["id"], "user", f"turn {i} " + "x" * 3000, images=[{"data": PNG, "mime": "image/png"}])
            self.chats.append(thread["id"], "assistant", f"reply {i} " + "y" * 3000)
        self.assertTrue(self.chats.needs_summary(thread["id"], limit_chars=20000))
        history = self.chats.history(thread["id"], limit_chars=20000)
        self.assertTrue(history[0]["content"][0]["text"].startswith("Earlier in this thread (summary):"))
        images = sum(1 for m in history for b in m["content"] if b["type"] == "image")
        omitted = sum(1 for m in history for b in m["content"] if b["type"] == "text" and b["text"] == "[photo omitted]")
        user_turns_kept = sum(1 for m in history if m["role"] == "user" and any(b.get("type") == "image" or b.get("text") == "[photo omitted]" for b in m["content"]))
        self.assertEqual(images, min(chats.RECENT_IMAGES, user_turns_kept))
        self.assertGreater(images, 0)
        self.assertGreaterEqual(omitted, 0)
        roles = [m["role"] for m in history]
        self.assertEqual(roles[0], "user")
        self.assertTrue(all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), "strict alternation")
        self.chats.set_summary(thread["id"], "They discussed twelve frames.")
        self.assertFalse(self.chats.needs_summary(thread["id"], limit_chars=20000))
        self.assertIn("twelve frames", self.chats.history(thread["id"], limit_chars=20000)[0]["content"][0]["text"])
        self.assertTrue(self.chats.older_text(thread["id"], 20000).startswith(f"{chats.USER_LABEL}: turn 0"))

    def test_path_guard(self):
        # ids are reduced to [a-z0-9-]; a traversal attempt becomes a plain name inside hq/os/chats
        self.assertEqual(self.chats.path("../../etc").name, "etc.md")
        with self.assertRaises(ValueError):
            self.chats.path("../../")
        with self.assertRaises(ValueError):
            self.chats.get("missing-thread")


if __name__ == "__main__":
    unittest.main()
