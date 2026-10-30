import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from aihq import arms


def _ts(offset_seconds=0):
    return datetime.fromtimestamp(time.time() - offset_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


class ArmsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home, self.domain = root / "home", root / "vault"
        self.claude = self.home / ".claude"
        (self.claude / "projects" / "-Users-x-vault").mkdir(parents=True)
        (self.domain / "hq").mkdir(parents=True)
        (self.domain / "agent" / "skills" / "ia-log").mkdir(parents=True)
        (self.domain / "agent" / "skills" / "ia-log" / "SKILL.md").write_text(
            "---\nname: ia-log\ndescription: Append one log line\n---\nAppend a line.\n")
        (self.domain / "hq" / "HQ.md").write_text(
            "---\ntype: hq\ndate: 2026-01-01\n---\n# HQ\n\n## Top 3\n\n- Pickup **OVERDUE 2d** · [[Show A]]\n\n"
            "## Waiting on you (blockers and rulings)\n\n- Ruling F1: size\n")
        (self.claude / "CLAUDE.md").write_text("Run from ~/does-not-exist-anywhere/ always.\n")
        self.patches = [patch.object(arms, "HOME", self.home), patch.object(arms, "CLAUDE", self.claude),
                        patch.object(arms, "DOMAIN", self.domain), patch.object(arms, "STATE", root / "state" / "arms.json")]
        for p in self.patches:
            p.start()
        arms.invalidate()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        arms.invalidate()
        self.tmp.cleanup()

    def _log(self, name, lines):
        path = self.claude / "projects" / "-Users-x-vault" / name
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")

    def test_today_reads_hq_and_flags_stale_build(self):
        cards, kpis = arms.today_cards()
        self.assertEqual(cards[0]["status"], "warn")
        self.assertIn("rebuild-hq", cards[0]["actions"])
        self.assertTrue(any(c["status"] == "overdue" and "Show A" in c["name"] for c in cards))
        self.assertEqual(dict(kpis)["Waiting on you"], "1")

    def test_usage_dedupes_streamed_message_ids_and_marks_cold_cache(self):
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000, "output_tokens": 5}
        message = {"id": "m1", "model": "claude-test", "usage": usage,
                   "content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "ia-log"}}]}
        self._log("s1.jsonl", [
            {"type": "user", "timestamp": _ts(7300), "message": {"content": "what is today"}},
            {"type": "assistant", "timestamp": _ts(7200), "message": message},
            {"type": "assistant", "timestamp": _ts(7200), "message": message},
        ])
        sessions = arms.sessions(30)
        self.assertEqual(sessions[0]["totals"]["cache_read_input_tokens"], 1000)
        self.assertEqual(sessions[0]["skills"]["ia-log"], 1)
        cards, kpis = arms.usage_cards()
        self.assertEqual(dict(kpis)["Cold caches"], "1")
        week = next(c for c in cards if c["id"] == "use-last-7-days")
        self.assertIn("set rates", dict(week["fields"])["API-price estimate"])

    def test_cost_appears_only_when_rates_are_set(self):
        totals = arms.Counter({"input_tokens": 1_000_000, "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0, "output_tokens": 0})
        with patch.object(arms, "config", return_value={}):
            self.assertIsNone(arms._cost(totals))
        rates = {"rates_per_million_tokens": {"input": 2, "cache_write": 0, "cache_read": 0, "output": 0}}
        with patch.object(arms, "config", return_value=rates):
            self.assertEqual(arms._cost(totals), 2)

    def test_dead_path_in_global_claude_md_becomes_a_reco(self):
        mem = next(m for m in arms.memory_files() if m["kind"] == "global")
        self.assertIn("~/does-not-exist-anywhere/", mem["broken"])
        self.assertTrue(any(r["kind"] == "Memory" for r in arms.recos()))

    def test_cart_and_notes_round_trip(self):
        recos = arms.recos()
        reco = next((r for r in recos if "copy" in (r.get("actions") or [])), recos[0])
        arms.act({"action": "cart-add", "id": reco["id"]})
        cards, kpis = arms.recos_cards()
        self.assertEqual(dict(kpis)["In cart"], "1")
        self.assertIn(reco["id"], [c["id"] for c in cards])
        arms.act({"action": "cart-clear"})
        self.assertEqual(arms.load_state()["cart"], [])
        arms.act({"action": "note", "skill": "ia-log", "text": "Use the agent name, not the model"})
        self.assertTrue(any(r["id"] == "apply-ia-log" for r in arms.recos()))
        with self.assertRaises(ValueError):
            arms.act({"action": "note", "skill": "ia-log", "text": ""})
        with self.assertRaises(ValueError):
            arms.act({"action": "delete-everything"})

    def test_small_skills_collapse_into_one_reco(self):
        pins = [r for r in arms.recos() if r["kind"] == "Skill" and r["id"].startswith("pin")]
        self.assertEqual(len(pins), 1)
        self.assertIn("agent/skills/ia-log/SKILL.md", pins[0]["prompt"])

    def test_a_failing_reader_shows_an_error_card_not_a_crash(self):
        with patch.dict(arms.BUILDERS, {"today": lambda: 1 / 0}):
            cards, kpis, provenance = arms.domain("today")
        self.assertEqual(cards[0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
