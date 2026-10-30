"""Agents, skills, agent worktrees and Git status for the dashboard.

Everything sits on the existing runtime: agent work is an `agent.prompt` task with its usual
approval, and edit-mode work runs in a Git worktree on its own `agent/...` branch, committed
locally and never pushed. Agent and skill definitions are files you review and commit.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import arms
from .brain import identifier, text

ADAPTERS = ("claude", "codex", "hermes", "ollama", "devin")
_git_cache: dict[str, tuple[float, dict]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def availability() -> list[dict]:
    """A PATH lookup, timestamped. It says whether a CLI is present, not whether it is signed in."""
    checked = _now()
    out = []
    for adapter in ADAPTERS:
        if adapter == "devin":
            status, detail = "handoff-only", "Portable task packets only (./hq handoff)."
        elif adapter == "ollama":
            status, detail = "local-server", "Runs only if Ollama is serving on 127.0.0.1:11434."
        else:
            path = shutil.which(adapter)
            status, detail = ("installed", path) if path else ("offline", "Not on PATH on this Mac.")
        out.append({"adapter": adapter, "status": status, "detail": detail, "checked": checked})
    return out


def agents(catalog: dict, tasks: list[dict]) -> list[dict]:
    present = {item["adapter"]: item for item in availability()}
    out = []
    for agent in catalog["agents"]:
        mine = [t for t in tasks if t["operation"] == "agent.prompt"
                and (t["payload"].get("agent") == agent["id"]
                     or (not t["payload"].get("agent") and t["payload"].get("adapter") == agent["id"]))]
        running = next((t for t in mine if t["status"] == "running"), None)
        failed = next((t for t in mine if t["status"] == "failed"), None)
        last = mine[0] if mine else None
        adapter = present.get(agent["adapter"], {})
        out.append({"id": agent["id"], "role": agent.get("role", ""), "adapter": agent["adapter"],
                    "skills": agent.get("skills", []), "availability": adapter.get("status", "unknown"),
                    "checked": adapter.get("checked"), "state": "running" if running else adapter.get("status", "unknown"),
                    "edits": agent["adapter"] in {"claude", "codex"},
                    "last_run": {"title": last["title"], "status": last["status"], "at": last["updated"]} if last else None,
                    "last_error": {"message": (failed.get("error") or "")[:400], "at": failed["updated"]} if failed else None})
    return out


# ---------- agent proposals (Git-reviewed) ----------

def proposals(root: Path) -> list[dict]:
    folder = root / "proposals" / "agents"
    out = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            out.append({**json.loads(path.read_text(encoding="utf-8")), "file": str(path.relative_to(root))})
        except (OSError, json.JSONDecodeError):
            continue
    return out


def propose(root: Path, payload: dict) -> dict:
    agent_id = identifier(payload.get("id"))
    adapter = payload.get("adapter")
    if adapter not in ADAPTERS:
        raise ValueError("Choose an adapter: " + ", ".join(ADAPTERS))
    skills = [identifier(s) for s in payload.get("skills", []) if isinstance(s, str) and s.strip()][:12]
    record = {"schema_version": 1, "id": agent_id, "role": text(payload.get("role"), 160), "adapter": adapter,
              "skills": skills, "permissions": ["tasks.read", "tasks.propose", "approvals.request"]}
    if (root / "agents" / f"{agent_id}.json").exists():
        raise FileExistsError("An agent with that id already exists.")
    folder = root / "proposals" / "agents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{agent_id}.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {"id": agent_id, "file": f"proposals/agents/{agent_id}.json"}


def accept(root: Path, agent_id: str) -> dict:
    agent_id = identifier(agent_id)
    source = root / "proposals" / "agents" / f"{agent_id}.json"
    target = root / "agents" / f"{agent_id}.json"
    if not source.is_file():
        raise ValueError("Proposal not found.")
    if target.exists():
        raise FileExistsError("An agent with that id already exists.")
    source.replace(target)
    return {"id": agent_id, "file": f"agents/{agent_id}.json"}


# ---------- skills ----------

def skills(tasks: list[dict]) -> list[dict]:
    uses = arms.Counter()
    for session in arms.sessions(30):
        uses.update(session["skills"])
    notes = arms.Counter(n["skill"] for n in arms.load_state()["notes"])
    out = []
    for s in sorted(arms.skill_files(), key=lambda s: -s["tokens"]):
        rel = s["path"].relative_to(arms.DOMAIN) if s["path"].is_relative_to(arms.DOMAIN) else s["path"]
        out.append({"name": s["name"], "description": s["description"], "source": s["source"], "file": str(rel),
                    "tokens": s["tokens"], "model": s["model"], "uses": uses.get(s["name"], 0), "notes": notes.get(s["name"], 0),
                    "small": not s["model"] and s["tokens"] < arms.SMALL_SKILL_TOKENS})
    return out


def create_skill(root: Path, payload: dict) -> dict:
    name = identifier(payload.get("name"))
    description = text(payload.get("description"), 400).replace("\n", " ")
    body = text(payload.get("body"), 20000)
    model = payload.get("model") or ""
    if model not in {"", "haiku", "sonnet", "opus"}:
        raise ValueError("Model must be haiku, sonnet, opus or empty.")
    home = {"os": root / ".agents" / "skills", "hermes": root.parent / "agent" / "skills"}.get(payload.get("home", "os"))
    if home is None:
        raise ValueError("Choose where the skill lives: os or hermes.")
    folder = home / name
    if folder.exists():
        raise FileExistsError("A skill with that name already exists.")
    folder.mkdir(parents=True)
    front = ["---", f"name: {name}", "description: " + json.dumps(description)] + ([f"model: {model}"] if model else []) + ["---", ""]
    (folder / "SKILL.md").write_text("\n".join(front) + body.strip() + "\n", encoding="utf-8")
    arms.invalidate()
    return {"name": name, "file": str((folder / "SKILL.md").relative_to(root.parent))}


# ---------- Git ----------

def _git(cwd: Path, *args: str, timeout: int = 30) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.strip()[:500] or "Git failed.")
    return result.stdout


def git_status(root: Path) -> dict:
    hit = _git_cache.get(str(root))
    if hit and time.time() - hit[0] < 15:
        return hit[1]
    try:
        lines = _git(root, "status", "--porcelain=v1", "-b").splitlines()
        head = lines[0][3:] if lines else ""
        branch = head.split("...")[0]
        ahead = re.search(r"ahead (\d+)", head)
        behind = re.search(r"behind (\d+)", head)
        agent_branches = [b.strip(" *+") for b in _git(root, "branch", "--list", "agent/*").splitlines()]
        reviews = []
        for name in agent_branches:
            stat = _git(root, "diff", "--shortstat", f"{branch}...{name}").strip()
            subject = _git(root, "log", "-1", "--format=%s · %cr", name).strip()
            reviews.append({"branch": name, "stat": stat or "no changes", "last": subject})
        status = {"available": True, "branch": branch, "changed": len(lines) - 1,
                  "ahead": int(ahead.group(1)) if ahead else 0, "behind": int(behind.group(1)) if behind else 0,
                  "agent_branches": agent_branches, "reviews": reviews}
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        status = {"available": False, "error": str(exc)[:200]}
    _git_cache[str(root)] = (time.time(), status)
    return status


AGENT_BRANCH = re.compile(r"agent/[a-z0-9][a-z0-9\-]{0,80}")


def _agent_branch(name) -> str:
    if not isinstance(name, str) or not AGENT_BRANCH.fullmatch(name):
        raise ValueError("Only agent/... branches can be reviewed here.")
    return name


def _worktree_of(top: Path, branch: str) -> Path | None:
    current = None
    for line in _git(top, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = Path(line[9:])
        elif line == f"branch refs/heads/{branch}":
            return current
    return None


def branch_diff(root: Path, name: str) -> dict:
    branch = _agent_branch(name)
    base = git_status(root).get("branch") or "HEAD"
    patch = _git(root, "diff", f"{base}...{branch}")
    return {"branch": branch, "base": base, "stat": _git(root, "diff", "--stat", f"{base}...{branch}").strip(),
            "patch": patch[:30000] + ("\n... diff cut at 30,000 characters" if len(patch) > 30000 else "")}


def branch_discard(root: Path, name: str) -> dict:
    branch = _agent_branch(name)
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    tree = _worktree_of(top, branch)
    if tree:
        _git(top, "worktree", "remove", "--force", str(tree))
    _git(top, "branch", "-D", branch)
    _git_cache.clear()
    return {"message": f"Discarded {branch} and its worktree."}


def branch_merge(root: Path, name: str) -> dict:
    """Merge an agent branch into your current branch, locally. Refuses if you have uncommitted changes."""
    branch = _agent_branch(name)
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    dirty = [line for line in _git(top, "status", "--porcelain").splitlines() if not line.startswith("??")]
    if dirty:
        raise ValueError("Commit or stash your own changes first; the merge needs a clean checkout.")
    current = _git(top, "rev-parse", "--abbrev-ref", "HEAD").strip()
    try:
        _git(top, "merge", "--no-ff", "-m", f"Merge {branch} (reviewed in the Agentic OS)", branch, timeout=120)
    except ValueError as exc:
        subprocess.run(["git", "merge", "--abort"], cwd=top, capture_output=True)
        raise ValueError(f"{branch} does not merge cleanly into {current}; nothing changed. {exc}")
    branch_discard(root, branch)
    return {"message": f"Merged {branch} into {current} locally. Push when you are ready."}


def worktree(root: Path, home: Path, task_id: str, title: str) -> dict:
    """A fresh worktree on its own branch, so an agent edit never touches your checkout."""
    top = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40] or "task"
    branch = f"agent/{slug}-{task_id[:8]}"
    path = home / "worktrees" / task_id[:12]
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(top, "worktree", "add", "-b", branch, str(path), "HEAD", timeout=120)
    return {"top": top, "path": path, "branch": branch, "cwd": path / root.relative_to(top)}


def finish_worktree(tree: dict, title: str) -> str:
    """Commit whatever the agent changed on its branch, locally. Never push."""
    path = tree["path"]
    _git(path, "add", "-A")
    stat = _git(path, "diff", "--cached", "--stat").strip()
    if not stat:
        _git(tree["top"], "worktree", "remove", "--force", str(path))
        _git(tree["top"], "branch", "-D", tree["branch"])
        return "No files changed, so no branch was kept."
    _git(path, "-c", "user.name=Agentic OS", "-c", "user.email=agentic-os@localhost", "commit", "-q", "-m",
         f"agent: {title[:120]}\n\nWritten by an approved Agentic OS task. Review before merging.")
    return (f"Changes committed on branch {tree['branch']} (not pushed). Review with: git diff main...{tree['branch']}\n"
            f"Worktree: {path}\n{stat}")
