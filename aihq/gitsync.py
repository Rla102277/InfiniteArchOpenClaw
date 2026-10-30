"""GitHub is the brain: commit what the OS wrote under hq/ and push it, on a schedule.

`brain.sync` stages only `hq/` (records, memory, inbox, runs, log, findings), commits when there
is something to commit, rebases onto the remote with your own uncommitted work autostashed, and
pushes the current branch. It never force-pushes, never stages anything outside PATHS, and backs
out cleanly if a rebase conflicts (the conflict is reported, nothing is lost).
Git calls that fail are reported as errors, never silently read as "nothing changed"; only the
transient iCloud "Resource deadlock avoided" failure is retried before giving up.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

PATHS = ["hq"]
TIMEOUT = 120
TRANSIENT = "Resource deadlock avoided"
TRANSIENT_RETRIES = 2
TRANSIENT_DELAY = 2.0


def _git(domain: Path, *args: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=domain, capture_output=True, text=True, timeout=timeout)
    for _ in range(TRANSIENT_RETRIES):
        if result.returncode == 0 or TRANSIENT not in result.stderr:
            break
        time.sleep(TRANSIENT_DELAY)
        result = subprocess.run(["git", *args], cwd=domain, capture_output=True, text=True, timeout=timeout)
    return result


def _ok(result: subprocess.CompletedProcess) -> bool:
    return result.returncode == 0


def branch(domain: Path) -> str:
    out = _git(domain, "rev-parse", "--abbrev-ref", "HEAD")
    return out.stdout.strip() if _ok(out) else ""


def sync(domain: Path, push: bool = True) -> dict:
    domain = Path(domain)
    if os.environ.get("AIHQ_NO_SYNC", "").lower() in {"1", "true", "yes"}:
        return {"skipped": "AIHQ_NO_SYNC is set (test or demo run); nothing committed or pushed"}
    if not (domain / ".git").exists():
        return {"error": "not a Git checkout"}
    current = branch(domain)
    if not current or current == "HEAD":
        return {"error": "detached HEAD; check out a branch first"}
    status = _git(domain, "status", "--porcelain", "--", *PATHS)
    if not _ok(status):
        return {"branch": current, "changed": 0, "committed": False, "pushed": False,
                "error": "status failed: " + (status.stderr.strip() or status.stdout.strip())[:400]}
    changed = [line for line in status.stdout.splitlines() if line.strip()]
    result = {"branch": current, "changed": len(changed), "committed": False, "pushed": False}
    if changed:
        add = _git(domain, "add", "-A", "--", *PATHS)
        if not _ok(add):
            return {**result, "error": add.stderr.strip()[:400]}
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        commit = _git(domain, "commit", "-q", "-m", f"hq: brain sync {stamp}\n\nWritten by the Agentic OS: records, memory, inbox, runs, log, findings.")
        if not _ok(commit):
            return {**result, "error": commit.stderr.strip()[:400] or commit.stdout.strip()[:400]}
        result["committed"] = True
    if not push:
        return result
    remote = _git(domain, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not _ok(remote):
        return {**result, "error": f"{current} has no upstream; run: git push -u origin {current}"}
    pull = _git(domain, "pull", "--rebase", "--autostash", "--quiet")
    if not _ok(pull):
        _git(domain, "rebase", "--abort")
        _git(domain, "stash", "pop")
        return {**result, "error": "pull --rebase failed, backed out: " + (pull.stderr.strip() or pull.stdout.strip())[:400]}
    ahead_res = _git(domain, "rev-list", "--count", "@{u}..HEAD")
    if not _ok(ahead_res):
        return {**result, "error": "rev-list failed: " + (ahead_res.stderr.strip() or ahead_res.stdout.strip())[:400]}
    ahead = ahead_res.stdout.strip()
    if ahead not in ("", "0"):
        pushed = _git(domain, "push", "--quiet")
        if not _ok(pushed):
            return {**result, "error": "push failed: " + pushed.stderr.strip()[:400]}
        result["pushed"] = True
        result["commits"] = int(ahead)
    return result
