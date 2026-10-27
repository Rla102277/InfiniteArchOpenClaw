import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aihq.brain import Brain, QUESTIONS, identifier, text


SOURCE = Path(__file__).resolve().parents[1]


def brain_fixture(directory):
    directory = Path(directory).resolve()
    repo = directory / "repo"
    repo.mkdir()
    for name in ("system", "apps", "themes", "workflows", "agents", "vault", ".agents"):
        shutil.copytree(SOURCE / name, repo / name, symlinks=True)
    shutil.copy2(SOURCE / "AGENTS.md", repo / "AGENTS.md")
    subprocess.run(
        ["git", "init", "--quiet", "--template=", str(repo)],
        check=True, capture_output=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
    )
    return repo, directory / "private-home"


def builder_payload(**overrides):
    payload = {
        "id": "integration-board", "name": "Integration board", "template": "project-board",
        "answers": {
            "problem": "Keep review work together.", "users": "The local owner.",
            "outcome": "Find the next review quickly.",
            "acceptance": "A saved record survives restart.",
        },
    }
    payload.update(overrides)
    return payload


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

    def test_preview_is_read_only_and_matches_explicit_build(self):
        before = repository_files(self.repo)
        preview = self.brain.preview(builder_payload())
        self.assertEqual(repository_files(self.repo), before)
        self.assertEqual(preview["manifest"]["permissions"], ["local.records"])
        self.assertEqual(preview["manifest"]["fields"], ["title", "status", "note"])
        self.assertEqual({question["id"] for question in preview["questions"]},
                         {question["id"] for question in QUESTIONS})
        self.assertTrue(preview["plan"])
        result = self.brain.build(builder_payload(confirm=True))
        self.assertEqual(result, preview)
        app = self.repo / "apps" / "integration-board"
        self.assertEqual(json.loads((app / "app.json").read_text()), preview["manifest"])
        self.assertEqual((app / "SPEC.md").read_text(), preview["spec"])
        self.assertIn("Rollback", (app / "HANDOFF.md").read_text())
        untracked = self.brain.git("ls-files", "--others", "--exclude-standard")
        self.assertIn("apps/integration-board/app.json", untracked)
        self.assertEqual(self.brain.revision()["head"], None)

    def test_create_requires_literal_confirmation_and_duplicate_is_unchanged(self):
        before = repository_files(self.repo)
        for confirmation in (None, False, 1, "true"):
            with self.subTest(confirmation=confirmation), self.assertRaises(ValueError):
                self.brain.build(builder_payload(confirm=confirmation))
            self.assertEqual(repository_files(self.repo), before)
        self.brain.build(builder_payload(confirm=True))
        built = repository_files(self.repo)
        with self.assertRaises(FileExistsError):
            self.brain.build(builder_payload(confirm=True))
        self.assertEqual(repository_files(self.repo), built)

    def test_failed_staged_build_leaves_no_partial_app_or_temporary_files(self):
        before = repository_files(self.repo)
        fingerprint = self.brain.fingerprint()
        original_write = Path.write_text
        destination = self.repo / "apps" / "integration-board"
        failures = []

        def fail_spec(filename, *args, **kwargs):
            if filename.name == "SPEC.md":
                failures.append(filename)
                self.assertTrue((filename.parent / "app.json").exists())
                self.assertFalse(destination.exists())
                raise OSError("Injected specification write failure")
            return original_write(filename, *args, **kwargs)

        with patch.object(Path, "write_text", new=fail_spec):
            with self.assertRaisesRegex(OSError, "specification write failure"):
                self.brain.build(builder_payload(confirm=True))
        self.assertEqual(len(failures), 1)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.repo.glob(".brain-build-*")), [])
        self.assertEqual(repository_files(self.repo), before)
        self.assertEqual(self.brain.fingerprint(), fingerprint)
        self.brain.build(builder_payload(confirm=True))
        self.assertTrue((destination / "SPEC.md").is_file())

    def test_failed_final_rename_removes_staging_and_allows_retry(self):
        before = repository_files(self.repo)
        with patch.object(Path, "rename", side_effect=OSError("Injected rename failure")):
            with self.assertRaisesRegex(OSError, "rename failure"):
                self.brain.build(builder_payload(confirm=True))
        self.assertEqual(repository_files(self.repo), before)
        self.assertEqual(list(self.repo.glob(".brain-build-*")), [])
        self.brain.build(builder_payload(confirm=True))

    def test_preview_rejects_incomplete_interviews_and_unknown_templates(self):
        for field in ("problem", "users", "outcome", "acceptance"):
            payload = builder_payload()
            del payload["answers"][field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.brain.preview(payload)
        for overrides in ({"answers": []}, {"template": "shell-script"}, {"name": ""}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.brain.preview(builder_payload(**overrides))

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

    def test_builder_does_not_write_through_symlinked_app_directory(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.repo / "apps" / "integration-board").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.brain.build(builder_payload(confirm=True))
        self.assertEqual(list(outside.iterdir()), [])

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
