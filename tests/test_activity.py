import tempfile
import unittest
from pathlib import Path

from aihq import activity


class ActivityTests(unittest.TestCase):
    def test_record_recent_and_sentences(self):
        domain = Path(tempfile.mkdtemp())
        activity.record(domain, "app:show-scout", "calendar", "create_event", "title: TPS Members Show \u2014 entries close; start: 2026-12-15", "Added", "done")
        activity.record(domain, "app:show-scout", "gmail", "send", "to: juror@example.org", "waiting", "queued")
        activity.record(domain, "operator", "telegram", "send", "text: hi", "HTTP 401", "failed")
        rows = activity.recent(domain)
        self.assertEqual([r["status"] for r in rows], ["failed", "queued", "done"])
        self.assertNotIn("\u2014", rows[-1]["args"])
        self.assertTrue(activity.sentence(rows[-1]).startswith("Show Scout put"))
        self.assertIn("waiting for your approval", activity.sentence(rows[1]))
        self.assertIn("failed", activity.sentence(rows[0]))
        log = (domain / "hq" / "log").glob("*.md")
        self.assertIn("calendar.create_event", next(log).read_text())


if __name__ == "__main__":
    unittest.main()
