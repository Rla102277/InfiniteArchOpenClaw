"""Project entrypoint; uses only Python standard library."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from . import core


def doctor() -> int:
    state = core.state()
    print("Personal AI HQ — environment check")
    print("Operating system:", sys.platform)
    for name, installed in state["tools"].items():
        print(f"  {'OK' if installed else 'MISSING':7} {name}")
    print(f"  {'OK' if sys.version_info >= (3, 10) else 'MISSING':7} Python >= 3.10")
    print("Skills:", len(state["skills"]))
    print("Projects:", len(state["projects"]))
    print("First-run steps: docs/ONBOARDING.md")
    # A missing AI tool is not a failure of the locally available dashboard.
    return int(sys.version_info < (3, 10))


def chat(project: str | None) -> int:
    if not shutil.which("hermes"):
        print("Hermes is not installed. See docs/ONBOARDING.md for the official installer.")
        return 2
    if project:
        try:
            folder = core.project_path(project)
            record = core.read_json(folder / "project.json")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        opening = (
            f"We are working on project '{record['name']}' in {folder}/. "
            "Read AGENTS.md and the project files. Resume from docs/HANDOFF.md and ask the next "
            "important discovery question. Do not implement until the owner approves the spec."
        )
    else:
        opening = (
            "Welcome the user to Personal AI HQ. Read AGENTS.md and START_HERE.md. "
            "If no project exists, explain how to create one through './hq start' or ask what they "
            "want to build. For an existing project, ask which to resume. One question at a time."
        )
    # Hermes' documented -q starts an interactive session on a TTY.
    return subprocess.call(["hermes", "chat", "-q", opening], cwd=core.ROOT)


def review(args) -> int:
    try:
        item = core.request_review(args.project, args.title, args.evidence, args.ref)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(f"Owner review requested: {item['id']}. Open ./hq start to see the approval card.")
    print("NOTE: An HQ approval is NOT a GitHub merge or production deployment.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="InfiniteArch Agentic OS — GitHub brain, private local runtime")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="Launch loopback HQ dashboard")
    start.add_argument("--port", type=int, default=8731)
    start.add_argument("--no-browser", action="store_true")
    sub.add_parser("doctor", help="Inspect installed tools")
    talk = sub.add_parser("chat", help="Talk to Hermes in this repository")
    talk.add_argument("--project", help="Project slug to resume")
    sub.add_parser("test", help="Run the built-in test suite")
    rev = sub.add_parser("request-review", help="Request human acceptance of a verified slice")
    rev.add_argument("--project", required=True)
    rev.add_argument("--title", required=True)
    rev.add_argument("--evidence", required=True, help="Relative path to test report or PR evidence")
    rev.add_argument("--ref", required=True, help="Commit/branch/PR reference")
    sub.add_parser("init", help="Initialize private local state and reconcile versioned definitions")
    sub.add_parser("state", help="Print local operational state as JSON (may contain private data)")
    sub.add_parser("validate", help="Validate portable brain contracts without creating local state")
    action = sub.add_parser("action", help="Submit a local JSON action, same contract as the dashboard")
    action.add_argument("payload", help='JSON object, e.g. {"action":"task.create",...}')
    worker = sub.add_parser("worker", help="Process tasks and interval schedules")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--allow-external", action="store_true", help="Allow individually approved agent processes/network calls; not a sandbox")
    worker.add_argument("--interval", type=int, default=5)
    worker.add_argument("--max-tasks", type=int, default=20)
    reconcile = sub.add_parser("reconcile", help="Validate and apply local declarations; optionally preview a GitHub pull")
    reconcile.add_argument("--pull", action="store_true")
    reconcile.add_argument("--confirm", action="store_true", help="Apply the previously previewed fast-forward pull")
    handoff = sub.add_parser("handoff", help="Emit a portable packet; private operational content is excluded by default")
    handoff.add_argument("--task", help="Explicitly include a task and its private prompt; review before sharing")
    backup = sub.add_parser("backup", help="Create a consistent local SQLite backup, never upload")
    backup.add_argument("--confirm", action="store_true", help="Without this flag, preview the backup destination")
    sub.add_parser("hermes-plan", help="Show safe installation and local-model configuration steps; never installs")
    args = parser.parse_args(argv)
    if args.command == "start":
        from .server import serve
        serve(port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "chat":
        return chat(args.project)
    if args.command == "request-review":
        return review(args)
    if args.command == "test":
        return subprocess.call([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=core.ROOT)
    if args.command == "validate":
        from .brain import Brain
        try:
            catalog = Brain().catalog()
            print(json.dumps({"valid": True, "fingerprint": Brain().fingerprint(),
                              "counts": {key: len(value) for key, value in catalog.items() if isinstance(value, list)}}, indent=2))
            return 0
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if args.command == "hermes-plan":
        print("Install the official signed Apple Silicon package using normal macOS security prompts:\n"
              "https://hermes-agent.nousresearch.com/docs/getting-started/installation\n"
              "No installer is downloaded or executed by HQ. Stop and verify the publisher if macOS warns.\n\n"
              "After installation: hermes model\n"
              "For local inference choose Custom Endpoint, http://127.0.0.1:11434/v1, no key,\n"
              "and an already downloaded tool-capable Ollama model. Configure server context\n"
              "to meet Hermes requirements; verify memory fit on your machine.\n"
              "Keep provider secrets in Hermes' private configuration, never in Git.\n"
              "Try hermes chat interactively before approving a queued adapter run.\n"
              "Local model requests may still use network tools in Hermes; audit its tool permissions.\n"
              "See docs/HERMES.md for version and trust details.")
        return 0
    if args.command in {"init", "state", "action", "worker", "reconcile", "handoff", "backup"}:
        from .runtime import Runtime
        try:
            runtime = Runtime()
            if args.command == "init":
                result = runtime.reconcile()
            elif args.command == "state":
                result = runtime.snapshot()
            elif args.command == "action":
                result = runtime.action(json.loads(args.payload))
            elif args.command == "reconcile":
                if args.confirm and not args.pull:
                    raise ValueError("--confirm is only used with --pull.")
                if args.pull:
                    from .gitops import sync
                    result = sync(runtime, args.confirm)
                else:
                    result = runtime.reconcile()
            elif args.command == "handoff":
                result = runtime.handoff(args.task)
            elif args.command == "backup":
                destination = runtime.home / "exports" / f"os-backup-{time.time_ns()}.sqlite3"
                result = {"destination": str(destination), "written": args.confirm,
                          "privacy": "Contains private local data. Encrypt and store separately from Git."}
                if args.confirm:
                    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(descriptor)
                    source = sqlite3.connect(runtime.database)
                    target = sqlite3.connect(destination)
                    try:
                        source.backup(target)
                    finally:
                        target.close()
                        source.close()
            else:
                if not 1 <= args.interval <= 60 or not 1 <= args.max_tasks <= 100:
                    raise ValueError("Worker interval must be 1–60 seconds and batch size 1–100.")
                while True:
                    result = runtime.tick(allow_external=args.allow_external, max_tasks=args.max_tasks)
                    if args.once:
                        break
                    if result["processed"]:
                        print(json.dumps(result), flush=True)
                    time.sleep(args.interval)
            print(json.dumps(result, indent=2))
            return 0
        except KeyboardInterrupt:
            return 130
        except (ValueError, TypeError, OSError, RecursionError, sqlite3.Error, subprocess.SubprocessError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
