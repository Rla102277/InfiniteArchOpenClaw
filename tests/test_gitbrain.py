"""Git is the brain: durable state lives in the domain repository, SQLite is a rebuildable cache."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from aihq.gitbrain import GitStore
from aihq.runtime import Runtime

SOURCE = Path(__file__).resolve().parents[1]
GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def domain_fixture(directory, config=None):
    """A domain repo with the OS inside it at os/, like theinfinitearch."""
    directory = Path(directory).resolve()
    domain = directory / "domain"
    os_root = domain / "os"
    os_root.mkdir(parents=True)
    for name in ("system", "apps", "themes", "workflows", "agents", "vault", ".agents"):
        if (SOURCE / name).is_dir():
            shutil.copytree(SOURCE / name, os_root / name, symlinks=True)
        else:
            (os_root / name).mkdir()
    shutil.copy2(SOURCE / "AGENTS.md", os_root / "AGENTS.md")
    if config is not False:
        (os_root / "curator.json").write_text(json.dumps(config or {"schema_version": 1, "storage": "git"}))
    subprocess.run(["git", "init", "--quiet", "--template=", str(domain)], check=True, capture_output=True, env=GIT_ENV)
    return domain, os_root


class GitBrainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.domain, self.os_root = domain_fixture(self.base)

    def runtime(self, home="home-a"):
        runtime = Runtime(root=self.os_root, home=self.base / home)
        runtime.reconcile()
        return runtime

    def test_record_files_in_git_rebuild_a_fresh_cache(self):
        """Board records are files under hq/os/records (the Curator app writes them); a new cache reads them back."""
        store = GitStore.load(self.os_root)
        store.write_record("works", "cdl-a-001", "Where Trolls Go to Watch the Sun Die", "A", "Grade: A", 0)
        path = self.domain / "hq/os/records/works/cdl-a-001.md"
        self.assertTrue(path.is_file())
        self.assertIn("Where Trolls Go", path.read_text())
        rebuilt = self.runtime("home-b").rebuild_from_git()
        self.assertEqual(rebuilt["records"], 1)

    def test_module_apps_are_always_enabled(self):
        app = self.os_root / "apps" / "test-app"
        app.mkdir(parents=True, exist_ok=True)
        manifest = {"schema_version": 1, "id": "test-app", "name": "Test app", "kind": "module",
                    "description": "Fixture module app.", "version": "1.0", "icon": "x",
                    "permissions": [], "views": [{"id": "board", "label": "Board"}], "web": {"script": "web/app.js"}, "entry": "app"}
        (app / "app.json").write_text(json.dumps(manifest))
        (app / "app.py").write_text("def snapshot(runtime):\n    return {}\n")
        (app / "web").mkdir()
        (app / "web" / "app.js").write_text("// fixture\n")
        status = {a["id"]: a["status"] for a in self.runtime("home-a").snapshot()["apps"]}
        self.assertEqual(status, {"test-app": "enabled"})

    def test_memory_lands_in_git_and_rebuilds(self):
        self.runtime("home-a").action({"action": "memory.put", "key": "Morning focus", "content": "Grade Franconia."})
        self.assertIn("Grade Franconia.", (self.domain / "hq/os/memory/morning-focus.md").read_text())
        memories = self.runtime("home-b").snapshot()["memories"]
        self.assertEqual([(m["key"], m["content"]) for m in memories], [("Morning focus", "Grade Franconia.")])

    def test_activity_is_logged_to_hq_log_without_em_dashes(self):
        runtime = self.runtime("home-a")
        runtime.action({"action": "memory.put", "key": "note", "content": "A — B"})
        logs = list((self.domain / "hq/log").glob("*.md"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text()
        self.assertIn(" · os · memory_saved: ", text)
        self.assertNotIn("—", text)

    def test_preferences_follow_git(self):
        self.runtime("home-a").action({"action": "preferences.set", "theme": "midnight", "panels": ["apps", "memory"]})
        prefs = self.runtime("home-b").snapshot()["preferences"]
        self.assertEqual(prefs, {"theme": "midnight", "panels": ["apps", "memory"]})

    def test_snapshot_reports_storage_mode(self):
        self.assertEqual(self.runtime().snapshot()["storage"], "git")

    def test_secrets_and_cache_stay_out_of_the_repository(self):
        runtime = self.runtime("home-a")
        self.assertFalse(runtime.database.resolve().is_relative_to(self.domain))
        self.assertFalse(list(self.domain.rglob("*.sqlite3")))

    def test_paths_cannot_escape_the_domain(self):
        (self.os_root / "curator.json").write_text(json.dumps({"schema_version": 1, "storage": "git", "data_dir": "../outside"}))
        with self.assertRaises(ValueError):
            GitStore.load(self.os_root)

    def test_bad_identifiers_are_rejected(self):
        store = GitStore.load(self.os_root)
        with self.assertRaises(ValueError):
            store.write_record("../apps", "x", "t", "open", "", 0)

    def test_generated_records_replace_only_their_own_source(self):
        store = GitStore.load(self.os_root)
        store.write_record("rulings", "mine", "My note", "open", "", 1, source="user")
        store.replace_generated("rulings", [{"id": "f1", "title": "One", "status": "open"},
                                            {"id": "f2", "title": "Two", "status": "open"}], source="canon")
        store.replace_generated("rulings", [{"id": "f2", "title": "Two", "status": "open"}], source="canon")
        ids = sorted(r["id"] for r in store.records())
        self.assertEqual(ids, ["f2", "mine"])


class OptOutTests(unittest.TestCase):
    def test_env_switch_turns_git_brain_off(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, os_root = domain_fixture(Path(temporary))
            with unittest.mock.patch.dict(os.environ, {"AIHQ_GIT_BRAIN": "off"}):
                self.assertIsNone(GitStore.load(os_root))


class PrivateModeUnchangedTests(unittest.TestCase):
    def test_without_curator_json_nothing_is_written_to_the_domain(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            domain, os_root = domain_fixture(base, config=False)
            runtime = Runtime(root=os_root, home=base / "home")
            runtime.reconcile()
            runtime.action({"action": "memory.put", "key": "k", "content": "v"})
            self.assertFalse((domain / "hq").exists())
            self.assertEqual(runtime.snapshot()["storage"], "private")


if __name__ == "__main__":
    unittest.main()


class RecordTimestampTests(unittest.TestCase):
    def test_iso_and_epoch_created_both_load(self):
        import tempfile
        from pathlib import Path
        from aihq import gitbrain
        self.assertEqual(gitbrain._epoch(0), 0.0)
        self.assertEqual(gitbrain._epoch("1700000000"), 1700000000.0)
        self.assertGreater(gitbrain._epoch("2026-10-29T00:48:53"), 1_700_000_000)
        self.assertEqual(gitbrain._epoch("garbage"), 0.0)
