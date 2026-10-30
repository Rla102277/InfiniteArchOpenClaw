"""Reviewed, fast-forward-only GitOps synchronization. No automatic push."""
from __future__ import annotations

import re

from .brain import digest


def sync(runtime, confirm=False):
    brain = runtime.brain
    if brain.git("status", "--porcelain"):
        raise ValueError("Commit or set aside local changes before pulling brain updates.")
    remote = brain.git("remote", "get-url", "origin")
    if not re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", remote):
        raise ValueError("Only an explicit github.com origin is supported by this sync command.")
    upstream = brain.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    branch = brain.git("symbolic-ref", "--quiet", "HEAD")
    if not upstream.startswith("origin/"):
        raise ValueError("The current branch must track origin.")
    brain.git("-c", "core.hooksPath=/dev/null", "fetch", "--no-tags", "origin")
    head = brain.git("rev-parse", "HEAD")
    target = brain.git("rev-parse", upstream)
    if head == target:
        return {"status": "current", "head": head}
    brain.git("merge-base", "--is-ancestor", head, target)
    plan = {"from": head, "to": target, "branch": branch, "upstream": upstream, "origin": remote,
            "changes": brain.git("diff", "--stat", head, target)}
    signature = digest(plan)
    with runtime.connect() as connection:
        previous = runtime.meta(connection, "pull_plan")
        if not confirm:
            runtime.set_meta(connection, "pull_plan", signature)
            return {"status": "preview", **plan, "next": "Review git diff HEAD..@{u}; rerun reconcile --pull --confirm."}
        if previous != signature:
            raise ValueError("Preview the exact pull first. Remote or local state changed since the last preview.")
    if brain.git("symbolic-ref", "--quiet", "HEAD") != branch or brain.git("rev-parse", "HEAD") != head or brain.git("status", "--porcelain"):
        raise ValueError("Checkout changed after preview validation; start a new pull preview.")
    brain.git("-c", "core.hooksPath=/dev/null", "merge", "--ff-only", target)
    result = runtime.reconcile()
    return {"status": "applied", **plan, "reconciliation": result,
            "next": "Review updated runtime code before restarting; no downloaded hooks were run."}
