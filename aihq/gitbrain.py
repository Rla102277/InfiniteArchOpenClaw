"""Git-brain mode: durable state lives as files in the domain repository.

Off unless `curator.json` sits at the OS root (it is not one of the definition folders, so
generic forks and the test fixtures never pick it up). When on:

- records, memory, app lifecycle and preferences are Markdown/JSON files under
  `<domain>/<data_dir>` (default `hq/os/`), readable in Obsidian and reviewed in Git;
- meaningful activity is appended to `<domain>/hq/log/YYYY-MM-DD.md` in the ia-log format;
- SQLite stays a disposable cache: tasks in flight, approvals, fast lookups. A fresh clone
  rebuilds records, memory and app state from these files.

Secrets never come here. Nothing in this module runs a command or touches the network.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

CONFIG = "curator.json"
SAFE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}")
QUIET_EVENTS = {"preferences.set", "brain_reconciled"}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            try:
                meta[key.strip()] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                meta[key.strip()] = value
    return meta, text[end + 5:]


def _epoch(value) -> float:
    """Records carry `created` as an epoch (module app boards) or an ISO string (agent apps). Never crash on it."""
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _render(meta: dict, body: str) -> str:
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items()] + ["---", ""]
    return "\n".join(lines) + body.rstrip() + "\n"


class GitStore:
    """File-backed durable state inside the domain repository."""

    def __init__(self, domain: Path, data_dir: str = "hq/os", log_dir: str = "hq/log", agent: str = "os"):
        self.domain = Path(domain).resolve()
        self.data = self._inside(data_dir)
        self.log_dir = self._inside(log_dir)
        self.agent = agent

    @classmethod
    def load(cls, os_root: Path) -> "GitStore | None":
        if os.environ.get("AIHQ_GIT_BRAIN", "").lower() == "off":
            return None  # explicit opt-out, used by tests that run the real CLI
        path = Path(os_root) / CONFIG
        if not path.is_file() or path.is_symlink():
            return None
        config = json.loads(path.read_text(encoding="utf-8"))
        if config.get("schema_version") != 1 or config.get("storage") != "git":
            raise ValueError("curator.json must declare schema_version 1 and storage 'git'.")
        domain = (Path(os_root) / config.get("domain_root", "..")).resolve()
        if not (domain / ".git").exists():
            raise ValueError("Git-brain domain root must be a Git checkout.")
        return cls(domain, config.get("data_dir", "hq/os"), config.get("log_dir", "hq/log"), config.get("agent", "os"))

    def _inside(self, relative: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Git-brain paths must stay inside the domain repository.")
        path = (self.domain / candidate).resolve()
        if not path.is_relative_to(self.domain):
            raise ValueError("Git-brain path escaped the domain repository.")
        return path

    @staticmethod
    def _id(value: str) -> str:
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ValueError("Git-brain identifiers are lowercase letters, digits and hyphens.")
        return value

    # records -----------------------------------------------------------------
    def record_path(self, app_id: str, record_id: str) -> Path:
        return self.data / "records" / self._id(app_id) / f"{self._id(record_id)}.md"

    def write_record(self, app_id, record_id, title, status, note, created, source="user", extra=None):
        meta = {"id": record_id, "app": app_id, "title": title, "status": status,
                "created": created, "source": source, **(extra or {})}
        _atomic_write(self.record_path(app_id, record_id), _render(meta, note or ""))

    def records(self):
        root = self.data / "records"
        out = []
        if not root.is_dir():
            return out
        for path in sorted(root.glob("*/*.md")):
            if path.is_symlink():
                continue
            try:
                meta, body = _frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if not meta.get("id") or not meta.get("title"):
                continue
            out.append({"id": str(meta["id"]), "app_id": path.parent.name, "title": str(meta["title"]),
                        "status": str(meta.get("status", "open")), "note": body.strip(),
                        "created": _epoch(meta.get("created")), "source": meta.get("source", "user")})
        return out

    def replace_generated(self, app_id: str, items: list[dict], source: str) -> int:
        """Rewrite every record of `app_id` that came from `source`; user records are untouched."""
        folder = self.data / "records" / self._id(app_id)
        keep = set()
        for item in items:
            self.write_record(app_id, item["id"], item["title"], item["status"], item.get("note", ""),
                              item.get("created", 0), source=source, extra=item.get("extra"))
            keep.add(item["id"])
        if folder.is_dir():
            for path in folder.glob("*.md"):
                meta, _ = _frontmatter(path.read_text(encoding="utf-8"))
                if meta.get("source") == source and path.stem not in keep:
                    path.unlink()
        return len(items)

    # memory ------------------------------------------------------------------
    def memory_path(self, key: str) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:96] or "note"
        return self.data / "memory" / f"{slug}.md"

    def write_memory(self, key, content, updated):
        _atomic_write(self.memory_path(key), _render({"key": key, "updated": updated}, content))

    def memories(self):
        root = self.data / "memory"
        out = []
        if root.is_dir():
            for path in sorted(root.glob("*.md")):
                meta, body = _frontmatter(path.read_text(encoding="utf-8"))
                if meta.get("key"):
                    out.append({"key": str(meta["key"]), "content": body.strip(), "updated": float(meta.get("updated", 0) or 0)})
        return out

    # app lifecycle and preferences ---------------------------------------------
    def _json(self, name, default):
        path = self.data / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def app_states(self) -> dict:
        return self._json("apps.json", {})

    def set_app_state(self, app_id, status):
        states = self.app_states()
        states[self._id(app_id)] = status
        _atomic_write(self.data / "apps.json", json.dumps(states, indent=2, sort_keys=True) + "\n")

    def preferences(self):
        return self._json("preferences.json", None)

    def set_preferences(self, value):
        _atomic_write(self.data / "preferences.json", json.dumps(value, indent=2) + "\n")

    # activity ----------------------------------------------------------------
    def log(self, event: str, message: str, when: float | None = None) -> None:
        if event in QUIET_EVENTS:
            return
        moment = datetime.fromtimestamp(when) if when else datetime.now()
        message = " ".join(str(message).split()).replace("—", ",")
        line = f"- {moment:%H:%M} · {self.agent} · {event}: {message} · hq/os\n"
        path = self.log_dir / f"{moment:%Y-%m-%d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
