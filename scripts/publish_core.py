"""Publish the generic OS core to a target checkout (e.g. InfiniteArchOpenClaw).

Copies the core of this repo's os/ into the target, deleting files there that are no
longer in the core. Excluded (practice data that stays in this checkout): the Curator
and Darkroom apps, curator.json (per-checkout config), the practice agent declarations
and skills, local/runtime state and caches. --dry-run prints the plan only.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {"apps/curator", "apps/darkroom", ".local", "__pycache__", "runtime", "private",
                "secrets", "exports", "logs"}
EXCLUDE_FILES = {
    "curator.json",
    "README.md",  # per-repo document; the target keeps its own
    "agents/critic.json", "agents/great-curator.json", "agents/hermes.json",
    "agents/photographer.json", "agents/scholar.json",
    ".agents/skills/critic/SKILL.md", ".agents/skills/curator-router/SKILL.md",
    ".agents/skills/great-curator/SKILL.md", ".agents/skills/ia-log/SKILL.md",
    ".agents/skills/photographer/SKILL.md", ".agents/skills/scholar/SKILL.md",
}
EXCLUDE_DIR_PREFIXES = (".agents/skills/critic", ".agents/skills/curator-router",
                        ".agents/skills/great-curator", ".agents/skills/ia-log",
                        ".agents/skills/photographer", ".agents/skills/scholar")
KEEP = {"LICENSE", ".git", "docs/PUBLISH.md", "docs/THIRD_PARTY_SKILLS.md",
        "releases", "onboarding", "proposals", "projects"}


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    if rel in EXCLUDE_FILES or parts[0] in KEEP and False:
        return True
    if any(rel == d or rel.startswith(d + "/") for d in EXCLUDE_DIRS):
        return True
    if any(rel == d or rel.startswith(d + "/") for d in EXCLUDE_DIR_PREFIXES):
        return True
    if "__pycache__" in parts or rel.endswith(".pyc"):
        return True
    return False


def plan(target: Path) -> tuple[list, list, list]:
    copied, deleted, skipped = [], [], []
    for src in sorted(ROOT.rglob("*")):
        rel = src.relative_to(ROOT).as_posix()
        if _excluded(rel):
            continue
        if src.is_dir():
            continue
        dst = target / rel
        if not dst.exists() or not filecmp.cmp(src, dst, shallow=False):
            copied.append(rel)
    for dst in sorted(target.rglob("*")):
        if not dst.is_file():
            continue
        rel = dst.relative_to(target).as_posix()
        if rel.split("/")[0] in KEEP or rel in KEEP:
            skipped.append(rel)
            continue
        if not (ROOT / rel).exists() or _excluded(rel):
            deleted.append(rel)
    return copied, deleted, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    target = args.target.resolve()
    if not (target / ".git").exists():
        print("Target is not a Git checkout.", file=sys.stderr)
        return 2
    copied, deleted, skipped = plan(target)
    print(f"copy/update: {len(copied)}   delete: {len(deleted)}   keep-skip: {len(skipped)}")
    if args.dry_run:
        for rel in copied[:60]:
            print("  copy   ", rel)
        for rel in deleted[:60]:
            print("  delete ", rel)
        return 0
    for rel in copied:
        src, dst = ROOT / rel, target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for rel in deleted:
        (target / rel).unlink()
    for parent in sorted({(target / rel).parent for rel in deleted}, reverse=True):
        try:
            parent.rmdir()
        except OSError:
            pass
    print("done. Review with git status in the target, run its tests, then commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
