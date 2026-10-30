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
    print("InfiniteArch Agentic OS: environment check")
    print("Operating system:", sys.platform)
    for name, installed in state["tools"].items():
        print(f"  {'OK' if installed else 'MISSING':7} {name}")
    print(f"  {'OK' if sys.version_info >= (3, 10) else 'MISSING':7} Python >= 3.10")
    print("Skills:", len(state["skills"]))
    try:
        from .brain import Brain
        from . import modules
        brain = Brain()
        brain.catalog()
        rows = modules.doctor(brain)
        if rows:
            print("Apps:")
            for row in rows:
                print(f"  {row['status']:7} {row['name']:28} {row['detail']}")
        else:
            print("Apps: no module apps")
    except (ValueError, OSError) as exc:
        print(f"  FAIL    brain                        {exc}")
    print("First-run steps: docs/ONBOARDING.md")
    # A missing AI tool is not a failure of the locally available dashboard.
    return int(sys.version_info < (3, 10))


def app_command(app_id: str, argv: list[str]) -> int:
    """./hq app <id> [args...]: hand the rest of the line to the app's own CLI."""
    from .brain import Brain, identifier
    from . import modules
    from .runtime import Runtime
    try:
        runtime = Runtime()
        app = modules.get(runtime.brain, identifier(app_id), runtime)
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if getattr(app, "cli", None) is None:
        print(f"The {app_id} app has no command line.", file=sys.stderr)
        return 2
    try:
        code = app.cli(list(argv))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # the app's own failure, reported, never a traceback into the terminal
        print(f"{app_id}: {exc}", file=sys.stderr)
        return 2
    return int(code or 0)


def chat_command(args) -> int:
    """One turn with the OS: an agent app (--app), a module agent (--agent) or the Operator."""
    if args.hermes:
        return chat()
    from .runtime import Runtime
    text_in = " ".join(args.text).strip()
    if not text_in:
        print("Say something: ./hq chat \"...\"", file=sys.stderr)
        return 2
    try:
        runtime = Runtime()
        result = runtime.action({"action": "chat.send", "thread": args.thread, "text": text_in,
                                 "agent": args.agent, "app": args.app})
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.get("message", "").rstrip())
    calls = result.get("tool_calls") or []
    usage = result.get("usage") or {}
    print(f"\n[{result.get('agent')} · thread {result.get('thread')} · {len(calls)} tool call(s)"
          f"{': ' + ', '.join(str(c.get('name')) for c in calls) if calls else ''}"
          f" · {usage.get('input', usage.get('input_tokens', 0))} in / {usage.get('output', usage.get('output_tokens', 0))} out]")
    return 0


def apps_command(args) -> int:
    from .brain import ROOT
    from . import agentapps
    domain = ROOT.parent
    try:
        if args.apps_command == "list":
            errors = {}
            apps = agentapps.load(domain, errors)
            for app in apps.values():
                cadence = app.spec["every"] or (("daily at " + app.spec["at"]) if app.spec["at"] else "on demand")
                print(f"{app.id:<24} {cadence:<14} {len(app.records()):>5} records  {app.spec['name']}")
            for app_id, error in errors.items():
                print(f"{app_id:<24} BROKEN  {error}")
            if not apps and not errors:
                print("No agent apps yet. ./hq apps create <id> --name ... --fields ... --prompt-file prompt.md")
            return 0
        if args.apps_command == "create":
            prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else (args.prompt or "")
            spec = {"id": args.id, "name": args.name or args.id.replace("-", " ").title(), "description": args.description or "",
                    "fields": [f.strip() for f in (args.fields or "title,url").split(",") if f.strip()], "key": args.key,
                    "every": args.every, "at": args.at, "web": bool(args.web), "notify": not args.quiet, "model": args.model}
            if args.icon:
                spec["icon"] = args.icon
            app = agentapps.create(domain, spec, prompt)
            print(f"created {app.id}: {app.spec['name']} ({app.spec['every'] or app.spec['at'] or 'on demand'}). Fields: {', '.join(app.spec['fields'])}.")
            try:
                from .runtime import Runtime
                Runtime().reconcile()
            except Exception as exc:
                print(f"(schedule registers on the next ./hq start: {exc})")
            return 0
        if args.apps_command == "run":
            from .runtime import Runtime
            result = agentapps.run(domain, args.id, runtime=Runtime())
            print(json.dumps(result, indent=2, default=str))
            return 0 if not result.get("error") else 1
        if args.apps_command == "delete":
            target = agentapps.delete(domain, args.id)
            print(f"retired {args.id}; moved to {target.relative_to(domain)} (records kept)")
            return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("./hq apps list | create <id> ... | run <id> | delete <id>")
    return 2


def chat() -> int:
    if not shutil.which("hermes"):
        print("Hermes is not installed. See docs/ONBOARDING.md for the official installer.")
        return 2
    opening = (
        "You are in the InfiniteArch Agentic OS repository. Read AGENTS.md. The practice runs through "
        "a module app: use `./hq app <id> ask \"...\"` to ask the app and relay its answer. One question at a time."
    )
    # Hermes' documented -q starts an interactive session on a TTY.
    return subprocess.call(["hermes", "chat", "-q", opening], cwd=core.ROOT)


def _pairs(items: list[str], what: str) -> dict:
    out = {}
    for item in items or []:
        key, sep, value = str(item).partition("=")
        if not sep or not key.strip():
            raise ValueError(f"{what} entries are KEY=value (or KEY=secret:<name>).")
        out[key.strip()] = value.strip()
    return out


def connectors_command(args) -> int:
    from .runtime import Runtime
    from . import connectors as conn
    runtime = Runtime()
    domain = runtime.brain.root.parent
    try:
        if args.connectors_cmd in (None, "list"):
            for item in runtime._connectors_list():
                status = item.get("status") or {}
                mark = "ok  " if status.get("ok") else "    "
                needs = f" needs: {', '.join(status.get('needs') or [])}" if status.get("needs") else ""
                print(f"{mark}{item['id']:<16} {item.get('kind', ''):<8} {item.get('tools', 0):>3} tools  {status.get('detail', '')}{needs}")
            return 0
        if args.connectors_cmd == "test":
            connector = conn.registry(domain).get(args.id)
            if connector is None:
                print(f"No connector {args.id}.", file=sys.stderr)
                return 1
            status = connector.status()
            print(json.dumps(status, indent=2))
            if status.get("ok"):
                print("tools:", ", ".join(t.get("name", "?") for t in connector.tools()))
            return 0 if status.get("ok") else 1
        if args.connectors_cmd == "add-mcp":
            spec = {"id": args.id, "name": args.name or args.id, "transport": "http" if args.url else "stdio",
                    "env": _pairs(args.env, "--env"), "headers": _pairs(args.header, "--header"), "enabled": True}
            if args.url:
                spec["url"] = args.url
            else:
                if not args.command:
                    raise ValueError("Give --command (stdio) or --url (http).")
                spec["command"], spec["args"] = args.command, list(args.args or [])
            print(json.dumps(conn.add_mcp(domain, spec), indent=2, default=str))
            return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("./hq connectors list | test <id> | add-mcp <id> ...", file=sys.stderr)
    return 2


def secret_command(args) -> int:
    import getpass
    from . import secrets as os_secrets
    name = getattr(args, "name", None)
    if not name:
        print("./hq secret set|delete|check <name>", file=sys.stderr)
        return 2
    if args.secret_cmd == "set":
        value = getpass.getpass(f"Value for {name} (not echoed): ")
        if not value.strip():
            print("Nothing saved.", file=sys.stderr)
            return 1
        os_secrets.put(name, value.strip())
        print(f"Saved {name}.")
        return 0
    if args.secret_cmd == "delete":
        os_secrets.delete(name)
        print(f"Deleted {name}.")
        return 0
    print(f"{name}: {'present' if os_secrets.present(name) else 'missing'}")
    return 0


def telegram_command() -> int:
    from .runtime import Runtime
    from . import telegram_gateway as tg
    try:
        gateway = tg.Gateway(Runtime(), log=lambda what: print(what, flush=True))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Telegram gateway in the foreground. Message your bot. Control-C to stop.", flush=True)
    try:
        while True:
            try:
                count = gateway.poll_once()
                if count:
                    print(f"{count} update(s) handled", flush=True)
            except Exception as exc:
                print(f"poll failed: {exc}", flush=True)
                time.sleep(5)
    except KeyboardInterrupt:
        return 130


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="InfiniteArch Agentic OS: GitHub brain, private local runtime")
    sub = parser.add_subparsers(dest="command")
    start = sub.add_parser("start", help="Launch loopback HQ dashboard")
    start.add_argument("--port", type=int, default=8731)
    start.add_argument("--no-browser", action="store_true")
    start.add_argument("--no-worker", action="store_true", help="Do not run approved tasks and schedules from the dashboard")
    sub.add_parser("doctor", help="Inspect installed tools")
    svc = sub.add_parser("install-service", help="macOS: start the OS at login (per-user LaunchAgent) and keep it running")
    svc.add_argument("--darkroom", action="store_true", help="also open IA Darkroom with its control port at login")
    svc.add_argument("--port", type=int, default=None)
    sub.add_parser("uninstall-service", help="macOS: remove the login agents")
    sub.add_parser("service-status", help="macOS: are the login agents loaded")
    talk = sub.add_parser("chat", help="Talk to the OS: an agent app (--app), a module agent (--agent) or the Operator; --hermes for Hermes")
    talk.add_argument("text", nargs="*")
    talk.add_argument("--thread", help="continue a thread id")
    talk.add_argument("--agent", help="operator, or an agent id offered by a module app")
    talk.add_argument("--app", help="an agent app id")
    talk.add_argument("--hermes", action="store_true", help="open an interactive Hermes session instead")
    apps = sub.add_parser("apps", help="Agent apps: apps made from a prompt and a schedule (hq/os/apps)")
    apps_sub = apps.add_subparsers(dest="apps_command")
    apps_sub.add_parser("list")
    mk = apps_sub.add_parser("create")
    mk.add_argument("id"); mk.add_argument("--name"); mk.add_argument("--description"); mk.add_argument("--icon")
    mk.add_argument("--every"); mk.add_argument("--at"); mk.add_argument("--fields", help="comma separated, e.g. title,venue,deadline,url")
    mk.add_argument("--key", default="url"); mk.add_argument("--web", action="store_true"); mk.add_argument("--quiet", action="store_true", help="no inbox note after runs")
    mk.add_argument("--model", default="default", choices=["default", "deep"])
    mk.add_argument("--prompt-file"); mk.add_argument("--prompt")
    apps_sub.add_parser("run").add_argument("id")
    apps_sub.add_parser("delete").add_argument("id")
    conn = sub.add_parser("connectors", help="Connected services: list, test one, add an MCP server")
    conn_sub = conn.add_subparsers(dest="connectors_cmd")
    conn_sub.add_parser("list")
    conn_sub.add_parser("test").add_argument("id")
    add = conn_sub.add_parser("add-mcp", help="./hq connectors add-mcp notion --command npx --args -y @notionhq/notion-mcp-server --env NOTION_TOKEN=secret:mcp-notion-NOTION_TOKEN")
    add.add_argument("id")
    add.add_argument("--name")
    add.add_argument("--command")
    add.add_argument("--args", nargs="*", default=[])
    add.add_argument("--url")
    add.add_argument("--env", action="append", default=[], help="KEY=value or KEY=secret:<name>")
    add.add_argument("--header", action="append", default=[], help="Name=value or Name=secret:<name>")
    sec = sub.add_parser("secret", help="Store a key in the Keychain (never in the repo)")
    sec_sub = sec.add_subparsers(dest="secret_cmd")
    sec_sub.add_parser("set").add_argument("name")
    sec_sub.add_parser("delete").add_argument("name")
    sec_sub.add_parser("check").add_argument("name")
    sub.add_parser("telegram", help="Run the Telegram gateway in the foreground (debugging)")
    sub.add_parser("test", help="Run the built-in test suite")
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
    sub.add_parser("sync", help="Module apps: run each app's sync routine (reconcile first)")
    app = sub.add_parser("app", help="Talk to a module app: ./hq app <id> [its own args...]")
    app.add_argument("app_id")
    app.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "app":
        return app_command(args.app_id, args.rest)
    if args.command == "start":
        from .server import serve
        serve(port=args.port, open_browser=not args.no_browser, worker=not args.no_worker)
        return 0
    if args.command in {"install-service", "uninstall-service", "service-status"}:
        from . import service
        from .brain import ROOT
        try:
            if args.command == "install-service":
                lines = service.install(ROOT, args.port, args.darkroom)
            elif args.command == "uninstall-service":
                lines = service.uninstall()
            else:
                lines = service.status()
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("\n".join(lines))
        return 0
    if args.command == "doctor":
        return doctor()
    if args.command == "chat":
        return chat_command(args)
    if args.command == "apps":
        return apps_command(args)
    if args.command == "connectors":
        return connectors_command(args)
    if args.command == "secret":
        return secret_command(args)
    if args.command == "telegram":
        return telegram_command()
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
    if args.command in {"init", "state", "action", "worker", "reconcile", "handoff", "backup", "sync"}:
        from .runtime import Runtime
        try:
            runtime = Runtime()
            if args.command == "init":
                result = runtime.reconcile()
            elif args.command == "sync":
                runtime.reconcile()
                from . import modules
                result = {app_id: out for app_id, app in modules.load(runtime.brain, runtime).items()
                          if not isinstance(app, dict)
                          and (out := modules.safe_call(app, "sync")) is not None}
                if not result:
                    raise ValueError("No installed module app offers a sync routine.")
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
                if not args.once and runtime.start_gateway():
                    print("Telegram: connected.", flush=True)
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
