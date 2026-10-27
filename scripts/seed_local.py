"""Idempotently seed a user's local, private HQ workspace (no network)."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aihq.storage import private_home, private_path
HOME = Path(os.environ.get("AIHQ_HOME", str(Path.home() / "PersonalAIHQ"))).expanduser()
if not HOME.is_absolute():
    raise SystemExit("AIHQ_HOME must be an absolute path")
FILES = {
    "README.md": "# Your Personal AI HQ\n\nThis is your private workspace. Start with `projects/` and `knowledge/`. The software lives in the starter Git repository.\n",
    "knowledge/README.md": "# Knowledge Vault\n\nHuman-authored notes live here. Agents must ask before modifying them. Never store passwords here.\n",
    "projects/README.md": "# Projects\n\nEach project gets a vision, requirements, decisions, plan, and handoff.\n",
    "projects/first-project/PROJECT.md": "# First Project\n\nStatus: Discovery\n\n## Idea\nDescribe what you want to build to Hermes.\n\n## First question\nWhat useful outcome do you want this project to achieve?\n",
    "projects/first-project/DECISIONS.md": "# Decisions\n\nRecord approved decisions and their reasons here.\n",
    "projects/first-project/HANDOFF.md": "# Session Handoff\n\nCurrent phase: discovery. Next: ask the owner one concrete question.\n",
    "runtime/README.md": "# Runtime\n\nLocal logs, operational databases, and transient state. Do not put secrets in Git.\n",
    "approvals/README.md": "# Owner Approvals\n\nA dashboard approval is not a GitHub merge or a production authorization.\n",
    "backups/README.md": "# Backups\n\nSet up encrypted backups before moving this system to an always-on machine.\n",
}

def seed(home: Path = HOME) -> tuple[int, int]:
    home = private_home(home)
    for name in FILES:
        private_path(home, name)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    created = skipped = 0
    for name, data in FILES.items():
        path = private_path(home, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            skipped += 1
        else:
            with path.open("x", encoding="utf-8") as f:
                f.write(data)
            created += 1
    for name in ("logs", "runtime", "inbox", "exports"):
        (home / name).mkdir(parents=True, exist_ok=True)
    return created, skipped

if __name__ == "__main__":
    created, skipped = seed()
    print(f"Seeded {HOME}: {created} files created; {skipped} already existed.")
