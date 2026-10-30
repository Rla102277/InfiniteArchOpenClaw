"""Portable declarations are data, never executable hooks."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
def text(value, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"Enter between 1 and {limit} characters.")
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
        raise ValueError("Identifiers must start with a letter and contain only lowercase letters, numbers and hyphens.")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Brain:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()

    def safe(self, relative):
        path = self.root / relative
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Brain path escapes the repository.")
        for parent in [path, *path.parents]:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise ValueError("Symlinks are not allowed in brain definitions.")
        return path

    def load(self, relative):
        path = self.safe(relative)
        if path.stat().st_size > 100000:
            raise ValueError(f"Definition too large: {relative}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Definition must be an object: {relative}")
        return data

    def catalog(self):
        config = self.load("system/os.json")
        if config.get("schema_version") != 1:
            raise ValueError("Unsupported system schema.")
        result = {"system": config, "apps": [], "themes": [], "workflows": [], "agents": []}
        for kind, pattern in [("apps", "apps/*/app.json"), ("themes", "themes/*.json"),
                              ("workflows", "workflows/*.json"), ("agents", "agents/*.json")]:
            for path in sorted(self.root.glob(pattern)):
                item = self.load(path.relative_to(self.root))
                item_id = identifier(item.get("id"))
                if (path.parent.name if kind == "apps" else path.stem) != item_id:
                    raise ValueError(f"Definition path must match id: {path}")
                text(item.get("name") if kind != "agents" else item.get("role"), 160)
                if kind == "apps":
                    text(item.get("description"), 10000)
                    if item.get("kind") == "module":
                        # A full application that mounts into the OS: system/contracts/MODULES.md
                        from . import modules
                        modules.manifest_ok(item, self.safe("apps/" + item_id))
                    elif (item.get("schema_version") != 1 or item.get("kind") != "record-board"
                            or item.get("fields") != ["title", "status", "note"]
                            or item.get("permissions") != ["local.records"]
                            or item.get("template") not in {"project-board", "reading-room", "daily-planner"}):
                        raise ValueError(f"Unsupported app contract: {item_id}")
                if kind == "workflows":
                    if (item.get("schema_version") != 1 or item.get("operation") not in {"digest.create", "agents.check", "brain.sync"}
                            or item.get("payload") != {}):
                        raise ValueError("Only the digest, agent check and brain sync workflows are supported.")
                    every = item.get("every")
                    if every is not None and not (isinstance(every, str) and re.fullmatch(r"\d+[mhd]", every)):
                        raise ValueError("A workflow's `every` is like 15m, 6h or 1d.")
                if kind == "themes":
                    colors = item.get("colors", {})
                    if set(colors) != {"background", "panel", "text", "muted", "accent"} or not all(
                            isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in colors.values()):
                        raise ValueError(f"Invalid theme colors: {item_id}")
                if kind == "agents":
                    if item.get("schema_version") != 1 or item.get("adapter") not in {"hermes", "codex", "claude", "devin", "ollama"}:
                        raise ValueError(f"Unsupported agent: {item_id}")
                if any(entry["id"] == item_id for entry in result[kind]):
                    raise ValueError("Duplicate definition identifier.")
                result[kind].append(item)
        if config.get("default_theme") not in {item["id"] for item in result["themes"]}:
            raise ValueError("Default theme not found.")
        return result

    def fingerprint(self):
        paths = [self.safe("AGENTS.md")]
        for folder in ["system", "apps", "themes", "agents", "workflows", "vault", ".agents/skills"]:
            paths.extend(self.safe(folder).rglob("*"))
        signatures = {}
        for path in sorted(paths):
            relative = path.relative_to(self.root)
            if any(part in {"__pycache__", ".local", ".pytest_cache"} or part.endswith(".pyc") for part in relative.parts):
                continue  # module apps are code; compiled caches must not read as brain drift
            checked = self.safe(relative)
            if checked.is_file():
                if checked.stat().st_size > 1000000:
                    raise ValueError(f"Brain file too large: {relative}")
                signatures[str(relative)] = hashlib.sha256(checked.read_bytes()).hexdigest()
        return digest(signatures)

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError(result.stderr.strip() or "Git operation failed.")
        return result.stdout.strip()

    def revision(self):
        try:
            return {"head": self.git("rev-parse", "HEAD"), "dirty": bool(self.git("status", "--porcelain"))}
        except (ValueError, FileNotFoundError):
            return {"head": None, "dirty": True}
