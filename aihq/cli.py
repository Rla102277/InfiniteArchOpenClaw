"""Project entrypoint; uses only Python standard library."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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
            record = core.read_json(core.project_path(project) / "project.json")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        opening = (
            f"We are working on project '{record['name']}' in projects/{project}/. "
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
    parser = argparse.ArgumentParser(description="Personal AI HQ starter")
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
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
