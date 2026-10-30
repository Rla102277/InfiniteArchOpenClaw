import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq.brain import Brain, identifier, text


SOURCE = Path(__file__).resolve().parents[1]


def brain_fixture(directory):
    directory = Path(directory).resolve()
    repo = directory / "repo"
    repo.mkdir()
    for name in ("system", "apps", "themes", "workflows", "agents", "vault", ".agents"):
        if (SOURCE / name).is_dir():
            shutil.copytree(SOURCE / name, repo / name, symlinks=True)
        else:
            (repo / name).mkdir()
    shutil.copy2(SOURCE / "AGENTS.md", repo / "AGENTS.md")
    # A record-board manifest (the shape the loader still validates; no such app ships any more).
    board = repo / "apps" / "project-board"
    board.mkdir()
    (board / "app.json").write_text(json.dumps(RECORD_BOARD, indent=2) + "\n", encoding="utf-8")
    subprocess.run(
        ["git", "init", "--quiet", "--template=", str(repo)],
        check=True, capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    return repo, directory / "private-home"


RECORD_BOARD = {"schema_version": 1, "id": "project-board", "name": "Project Board",
                "description": "Test fixture: a declarative record board.", "template": "project-board",
                "kind": "record-board", "fields": ["title", "status", "note"], "permissions": ["local.records"],
                "version": "1.0.0"}


def repository_files(repo):
    return {
        str(filename.relative_to(repo)): filename.read_bytes()
        for filename in repo.rglob("*")
        if filename.is_file()
    }


def write_json(filename, value):
    filename.write_text(json.dumps(value), encoding="utf-8")


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo, self.home = brain_fixture(self.temporary.name)
        self.brain = Brain(self.repo)

    def test_bundled_catalog_loads_from_separate_repository(self):
        catalog = self.brain.catalog()
        self.assertEqual(catalog["system"]["schema_version"], 1)
        self.assertIn("project-board", {app["id"] for app in catalog["apps"]})
        self.assertTrue(catalog["themes"])
        self.assertTrue(catalog["workflows"])
        self.assertTrue(catalog["agents"])
        self.assertNotEqual(self.repo, SOURCE)

    def test_identifiers_and_text_do_not_allow_path_or_nul_payloads(self):
        for value in ("../escape", "/tmp/escape", "MixedCase", "a/b", "a\\b", "a;whoami", "a" * 65, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                identifier(value)
        for value in (None, "", "  ", "a\x00b", "a" * 10001, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                text(value)
        self.assertEqual(text("  useful text  "), "useful text")

    def test_safe_rejects_traversal_and_absolute_escape(self):
        outside = Path(self.temporary.name) / "outside.json"
        outside.write_text('{"private": true}')
        for relative in ("../outside.json", "apps/../../outside.json", outside):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                self.brain.load(relative)

    def test_safe_rejects_internal_and_external_symlinks(self):
        inside = self.repo / "system" / "os.json"
        outside = Path(self.temporary.name) / "private.json"
        outside.write_text("{}")
        for target in (inside, outside):
            link = self.repo / "linked.json"
            link.symlink_to(target)
            try:
                with self.subTest(target=target), self.assertRaises(ValueError):
                    self.brain.load("linked.json")
            finally:
                link.unlink()
        (self.repo / "linked-directory").symlink_to(self.repo / "system", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.brain.load("linked-directory/os.json")

    def test_load_rejects_non_objects_invalid_json_and_oversized_definitions(self):
        filename = self.repo / "system" / "bad.json"
        for content in ("[]", "null", "not json", " " * 100001):
            filename.write_text(content)
            with self.subTest(content=content[:20]), self.assertRaises(ValueError):
                self.brain.load("system/bad.json")

    def test_catalog_rejects_unsupported_system_and_app_contracts(self):
        filename = self.repo / "system" / "os.json"
        original = json.loads(filename.read_text())
        for change in ({"schema_version": 99}, {"default_theme": "missing-theme"}):
            write_json(filename, {**original, **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.brain.catalog()
        write_json(filename, original)
        filename = self.repo / "apps" / "project-board" / "app.json"
        original = json.loads(filename.read_text())
        for change in (
            {"schema_version": 99}, {"id": "different-path"}, {"kind": "python-hook"},
            {"permissions": ["shell.execute"]}, {"fields": ["title", "command"]},
            {"template": "remote-template"},
        ):
            write_json(filename, {**original, **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.brain.catalog()

    def test_catalog_rejects_executable_workflows_invalid_agents_and_colors(self):
        for relative, changes in (
            ("workflows/daily-digest.json", ({"schema_version": 99}, {"operation": "shell.execute"}, {"payload": {"command": "whoami"}})),
            ("agents/codex.json", ({"schema_version": 99}, {"adapter": "unknown-provider"})),
            ("themes/midnight.json", ({"colors": {}}, {"colors": {"accent": "url(https://example.invalid)"}})),
        ):
            filename = self.repo / relative
            original = json.loads(filename.read_text())
            for change in changes:
                write_json(filename, {**original, **change})
                with self.subTest(relative=relative, change=change), self.assertRaises(ValueError):
                    self.brain.catalog()
            write_json(filename, original)

    def test_fingerprint_covers_instructions_vault_skills_and_declarations(self):
        original = self.brain.fingerprint()
        files = [self.repo / "AGENTS.md", self.repo / "vault" / "Memory.md",
                 self.repo / ".agents" / "skills" / "verify" / "SKILL.md",
                 self.repo / "apps" / "project-board" / "app.json"]
        for filename in files:
            content = filename.read_bytes()
            filename.write_bytes(content + b"\n")
            with self.subTest(filename=filename):
                self.assertNotEqual(self.brain.fingerprint(), original)
            filename.write_bytes(content)
        (self.repo / "unrelated.txt").write_text("Not a brain declaration")
        self.assertEqual(self.brain.fingerprint(), original)

    def test_fingerprint_rejects_oversized_files_and_symlinks(self):
        filename = self.repo / "vault" / "oversized.md"
        filename.write_bytes(b"a" * 1000001)
        with self.assertRaises(ValueError):
            self.brain.fingerprint()
        filename.unlink()
        filename.symlink_to(self.repo / "AGENTS.md")
        with self.assertRaises(ValueError):
            self.brain.fingerprint()


if __name__ == "__main__":
    unittest.main()
