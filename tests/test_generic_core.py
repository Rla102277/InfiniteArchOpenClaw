"""The publishable core stays generic: no owner names, no absolute checkout paths,
no practice app names outside a small, justified allowlist.

Scan covers os/aihq, os/web, os/system, os/themes, os/workflows, os/docs, os/agents
and os/.agents. apps/curator, apps/darkroom and curator.json are excluded by design
(this checkout's own practice lives there).
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = ("aihq", "web", "system", "themes", "workflows", "docs", "agents", ".agents")
PUBLISHED_DOCS = ("README.md", "START_HERE.md", "SYSTEM.md")
SKIP_SUFFIX = {".pyc", ".png", ".zip", ".icns", ".woff", ".woff2"}

TOKENS = {
    "owner-name": re.compile(r"\bRandy\b|\bArchambault\b", re.IGNORECASE),
    "abs-home": re.compile(r"/Users/[A-Za-z0-9_.-]+"),
    "curator": re.compile(r"\bcurator\b", re.IGNORECASE),
    "darkroom": re.compile(r"\bdarkroom\b", re.IGNORECASE),
    "practice-ids": re.compile(r"\bCDL[-\d]|\bP900\b"),
}

# (relative path, token) -> why it is allowed. Everything else fails.
ALLOW = {
    # curator.json is the per-checkout config file name (the mechanism itself), not practice content.
    ("aihq/gitbrain.py", "curator"): "names the config file the Git-brain mode reads",
    ("aihq/core.py", "curator"): "names the config file local_config() reads",
    ("aihq/basetools.py", "curator"): "docstring names the config file search_scopes reads",
    ("aihq/runtime.py", "curator"): "error message names the config file Git-brain mode needs",
    ("aihq/arms.py", "curator"): "docstring names the config file extra_recos reads",
    ("aihq/service.py", "curator"): "remaining hits are the config filename context",
    ("aihq/agentapps.py", "curator"): "docstring names the config file always_ask_terms reads",
    ("aihq/connectors.py", "curator"): "same",
    ("tests/test_basetools.py", "curator"): "fixture builds a curator module app dir",
    ("tests/test_gitbrain.py", "curator"): "fixture writes a curator.json config file",
    ("tests/test_bots.py", "darkroom"): "fake connector name in a policy fixture",
    ("tests/test_connectors.py", "darkroom"): "same",
    ("SYSTEM.md", "curator"): "the OS-019 row documents the remaining coupling by name",
    ("SYSTEM.md", "darkroom"): "same",
    ("README.md", "curator"): "the config filename curator.json is documented, not the app",
    # The secrets backend name is baked into stored Keychain items and CURATOR_* env vars;
    # renaming it would strand existing credentials. Needs a migration decision.
    ("aihq/secrets.py", "curator"): "Keychain service/env prefix in use by stored credentials",
    ("aihq/llm.py", "curator"): "same Keychain service name",
    # <app>.read/.write is the cross-app permission vocabulary; the namesake app is a module
    # app, but the permission strings are contract identifiers.
    ("aihq/modules.py", "darkroom"): "permission namespace in the modules contract",
    # Optional second LaunchAgent opens a companion app at login; making it config-driven
    # is a pending decision, flagged in the handoff.
    ("aihq/service.py", "darkroom"): "optional companion-app LaunchAgent flag",
    ("aihq/cli.py", "darkroom"): "its --darkroom install-service flag",
    # The Overview surface deep-links into two specific apps; making it app-agnostic is a
    # pending decision, flagged in the handoff.
    ("web/app.js", "curator"): "Overview panels are app-coupled by design today",
    ("web/app.js", "darkroom"): "same",
    ("web/app.js", "owner-name"): "'randy' is a stored actor id, not display text",
    ("web/index.html", "curator"): "same",
    ("web/index.html", "darkroom"): "same",
    # Practice definitions still sit in the shared catalog folders; where they live
    # (catalog vs app) is a pending decision, flagged in the handoff.
    ("agents/critic.json", "curator"): "practice agent declaration pending relocation",
    ("agents/great-curator.json", "curator"): "same",
    ("agents/hermes.json", "curator"): "same",
    ("agents/photographer.json", "curator"): "same",
    ("agents/scholar.json", "curator"): "same",
    (".agents/skills/critic/SKILL.md", "curator"): "practice skill pending relocation",
    (".agents/skills/curator-router/SKILL.md", "curator"): "same",
    (".agents/skills/great-curator/SKILL.md", "curator"): "same",
    (".agents/skills/ia-log/SKILL.md", "curator"): "same",
    (".agents/skills/photographer/SKILL.md", "curator"): "same",
    (".agents/skills/scholar/SKILL.md", "curator"): "same",
    # Placeholder, not a real account path.
    ("docs/MAC_MINI.md", "abs-home"): "the /Users/YOU placeholder is intentional",
    ("system/contracts/AGENT_RUNTIME.md", "curator"): "documents the Keychain service name",
    ("system/contracts/CONNECTORS.md", "curator"): "same",
    ("system/contracts/CONNECTORS.md", "owner-name"): "documents a stored actor id value",
}

# Whole-file allowlist: fully practice-authored skills/agent declarations also carry
# the other tokens (owner name, canon ids).
ALLOW_FILES = {
    ".agents/skills/critic/SKILL.md",
    ".agents/skills/curator-router/SKILL.md",
    ".agents/skills/great-curator/SKILL.md",
    ".agents/skills/ia-log/SKILL.md",
    ".agents/skills/photographer/SKILL.md",
    ".agents/skills/scholar/SKILL.md",
    "agents/critic.json",
    "agents/great-curator.json",
    "agents/hermes.json",
    "agents/photographer.json",
    "agents/scholar.json",
}


class GenericCoreTests(unittest.TestCase):
    def test_core_has_no_owner_specific_content(self):
        violations = []
        for folder in SCAN:
            for path in sorted(ROOT.joinpath(folder).rglob("*")):
                if not path.is_file() or path.suffix.lower() in SKIP_SUFFIX:
                    continue
                rel = path.relative_to(ROOT).as_posix()
                if rel in ALLOW_FILES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                for token, pattern in TOKENS.items():
                    if (rel, token) in ALLOW:
                        continue
                    if pattern.search(text):
                        for match in pattern.finditer(text):
                            line = text[:match.start()].count("\n") + 1
                            violations.append(f"{rel}:{line} [{token}] {match.group(0)}")
                            break
        self.assertEqual([], violations)

    def test_brand_is_present(self):
        for page in ("web/index.html", "web/command.html"):
            text = (ROOT / page).read_text(encoding="utf-8")
            self.assertIn("THE INFINITE ARCH", text, page)

    def _violations_in(self, paths):
        violations = []
        for path in paths:
            rel = path.relative_to(ROOT).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if rel == "tests/test_generic_core.py":
                continue  # this file's own token patterns and allowlist definitions
            for line_no, line in enumerate(text.splitlines(), 1):
                for token, pattern in TOKENS.items():
                    if pattern.search(line):
                        if ALLOW.get((rel, token)):
                            continue
                        violations.append(f"{rel}:{line_no} [{token}] {line.strip()[:80]}")
        return violations

    def test_published_docs_and_tests_stay_generic(self):
        if (ROOT / "curator.json").exists():
            docs = []  # this checkout's README documents the practice; the published one is generic
        else:
            docs = [ROOT / name for name in PUBLISHED_DOCS if (ROOT / name).exists()]
        paths = docs + sorted((ROOT / "tests").glob("*.py"))
        violations = [v for v in self._violations_in(paths) if "tests/test_generic_core.py" not in v]
        self.assertEqual([], violations)

    def test_no_literal_home_path_in_core(self):
        home = str(Path.home())
        violations = []
        for folder in SCAN:
            for path in sorted(ROOT.joinpath(folder).rglob("*")):
                if not path.is_file() or path.suffix.lower() in SKIP_SUFFIX:
                    continue
                try:
                    if home in path.read_text(encoding="utf-8"):
                        violations.append(path.relative_to(ROOT).as_posix())
                except (OSError, UnicodeDecodeError):
                    continue
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
