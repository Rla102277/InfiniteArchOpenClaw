"""Single-user local runtime with transactional tasks, approvals and memory."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import adapters
from .brain import Brain, digest, identifier, text
from .storage import private_home
from .gitbrain import GitStore
from . import agentops, arms, core, gitsync, modules, agentapps

PANELS = ["today", "apps", "agents", "tasks", "skills", "recos", "schedules", "routines", "memory", "usage", "activity", "system"]
ORIGINAL_PANELS = ["apps", "tasks", "schedules", "memory", "activity", "system"]
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL,
 status TEXT NOT NULL, result TEXT, error TEXT, created REAL NOT NULL, updated REAL NOT NULL,
 dedupe_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL REFERENCES tasks(id), title TEXT NOT NULL,
 status TEXT NOT NULL, adapter TEXT NOT NULL, prompt_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedules (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, workflow TEXT NOT NULL, interval_seconds INTEGER NOT NULL,
 enabled INTEGER NOT NULL, next_run REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, content TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL, event TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS apps (id TEXT PRIMARY KEY, status TEXT NOT NULL, manifest_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, app_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, created REAL NOT NULL);
"""


re_inbox_id = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}")


def _write_inbox_status(path, meta, body, status, note):
    meta = {**meta, "status": status, "decided": datetime.now().isoformat(timespec="seconds"), "note": str(note)[:500]}
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(agentapps.render(meta, body), encoding="utf-8")
    tmp.replace(path)


def new_id():
    return uuid.uuid4().hex


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


class Runtime:
    def __init__(self, root=None, home=None):
        self.brain = Brain(root) if root is not None else Brain()
        configured = Path(home or os.environ.get("AIHQ_HOME", str(Path.home() / "PersonalAIHQ"))).expanduser()
        self.home = private_home(configured, self.brain.root)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.home.chmod(0o700)
        for name in ["runtime", "private", "secrets", "logs", "exports"]:
            directory = self.home / name
            if directory.is_symlink():
                raise ValueError("Runtime subdirectories must not be symlinks.")
            directory.mkdir(exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        self.database = self.home / "runtime" / "os.sqlite3"
        if self.database.is_symlink():
            raise ValueError("Runtime database must not be a symlink.")
        descriptor = os.open(self.database, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        os.close(descriptor)
        self.database.chmod(0o600)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            if self.meta(connection, "schema_version", 1) != 1:
                raise ValueError("Unsupported local database schema; restore with a compatible runtime.")
            self.set_meta(connection, "schema_version", 1)
        self.git = GitStore.load(self.brain.root)
        if self.git:
            self.rebuild_from_git()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def set_meta(connection, key, value):
        connection.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    @staticmethod
    def meta(connection, key, default=None):
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def event(self, connection, event, message):
        connection.execute("INSERT INTO events(time,event,message) VALUES (?,?,?)", (time.time(), event, message))
        if getattr(self, "git", None):
            self.git.log(event, message)

    def rebuild_from_git(self):
        """Git is the brain: replace cached records and memory with what the repository holds."""
        records, memories = self.git.records(), self.git.memories()
        with self.connect() as connection:
            connection.execute("DELETE FROM records")
            for item in records:
                connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?)",
                                   (item["id"], item["app_id"], item["title"], item["status"], item["note"], item["created"] or time.time()))
            connection.execute("DELETE FROM memories")
            for item in memories:
                connection.execute("INSERT INTO memories VALUES (?,?,?,?)", (new_id(), item["key"], item["content"], item["updated"] or time.time()))
        return {"records": len(records), "memories": len(memories)}

    def reconcile(self):
        catalog = self.brain.catalog()
        fingerprint = self.brain.fingerprint()
        with self.connect() as connection:
            previous = self.meta(connection, "brain_fingerprint")
            present = {item["id"] for item in catalog["apps"]}
            for manifest in catalog["apps"]:
                row = connection.execute("SELECT * FROM apps WHERE id=?", (manifest["id"],)).fetchone()
                signature = digest(manifest)
                if row is None:
                    connection.execute("INSERT INTO apps VALUES (?,?,?)", (manifest["id"], "available", signature))
                elif row["manifest_hash"] != signature or row["status"] == "unavailable":
                    connection.execute("UPDATE apps SET status='available', manifest_hash=? WHERE id=?", (signature, manifest["id"]))
                    self.event(connection, "app_definition_changed", "App needs reinstall: " + manifest["id"])
            if getattr(self, "git", None):
                for app_id, status in self.git.app_states().items():
                    if app_id in present and status in {"installed", "enabled", "paused", "uninstalled"}:
                        connection.execute("UPDATE apps SET status=? WHERE id=?", (status, app_id))
            for row in connection.execute("SELECT id FROM apps").fetchall():
                if row["id"] not in present:
                    connection.execute("UPDATE apps SET status='unavailable' WHERE id=?", (row["id"],))
            module_routines = {r["workflow"]: r for r in modules.routines(self.brain, self)}
            for row in connection.execute("SELECT * FROM schedules WHERE enabled=1").fetchall():
                if row["workflow"] not in {item["id"] for item in catalog["workflows"]} and row["workflow"] not in module_routines:
                    connection.execute("UPDATE schedules SET enabled=0 WHERE id=?", (row["id"],))
            existing = {row["workflow"] for row in connection.execute("SELECT workflow FROM schedules").fetchall()}
            for item in catalog["workflows"]:
                every = item.get("every")
                if not every or item["id"] in existing:
                    continue
                seconds = modules.interval_seconds({"every": every, "at": None})
                if seconds:
                    connection.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?)",
                                       (new_id(), item.get("name", item["id"]), item["id"], seconds, 1, time.time() + 60))
                    self.event(connection, "schedule.create", f"Built-in routine scheduled: {item['id']} every {every}")
            try:
                never_ran = {f"module:{app_id}:run" for app_id, app in agentapps.load(self.brain.root.parent).items()
                             if app.last_run() is None}
            except Exception:  # a broken app definition must not stop the scheduler
                never_ran = set()
            for row in connection.execute("SELECT id, workflow, next_run FROM schedules WHERE enabled=1").fetchall():
                if row["workflow"] in never_ran and row["next_run"] > time.time() + 120:
                    connection.execute("UPDATE schedules SET next_run=? WHERE id=?", (time.time() + 60, row["id"]))
            for workflow, routine in module_routines.items():
                seconds = modules.interval_seconds(routine)
                signature = json.dumps({"every": routine.get("every"), "at": routine.get("at")}, sort_keys=True)
                if seconds is not None and workflow in existing:
                    # The app changed its cadence (say daily -> 07:00): move the schedule with it,
                    # keeping its enabled or paused state.
                    if self.meta(connection, f"cadence:{workflow}") != signature:
                        upcoming = time.time() + 60 if workflow in never_ran else modules.first_run(routine)
                        connection.execute("UPDATE schedules SET interval_seconds=?, next_run=? WHERE workflow=?",
                                           (seconds, upcoming, workflow))
                        if self.meta(connection, f"cadence:{workflow}") is not None:
                            self.event(connection, "schedule.update", f"Cadence changed: {workflow} {routine.get('at') or routine.get('every')}")
                        self.set_meta(connection, f"cadence:{workflow}", signature)
                    continue
                if seconds is None:
                    continue  # no cadence declared
                self.set_meta(connection, f"cadence:{workflow}", signature)
                connection.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?)",
                                   (new_id(), f"{routine['app']}: {routine['name']}", workflow, seconds, 1, modules.first_run(routine)))
                self.event(connection, "schedule.create", f"Module routine scheduled: {workflow}")
            self.set_meta(connection, "brain_fingerprint", fingerprint)
            self.set_meta(connection, "reconciled_head", self.brain.revision()["head"])
            if previous != fingerprint:
                self.event(connection, "brain_reconciled", "Validated versioned declarations and updated local catalog.")
        return {"fingerprint": fingerprint, "apps": len(catalog["apps"]), "changed": previous != fingerprint}

    def snapshot(self):
        catalog = self.brain.catalog()
        fingerprint = self.brain.fingerprint()
        with self.connect() as connection:
            apps = [{**item, "status": "enabled"} for item in catalog["apps"] if item.get("kind") == "module"]
            try:
                apps += [{**manifest, "status": "enabled"} for manifest in agentapps.manifests(self.brain.root.parent, self).values()]
            except Exception:
                pass
            tasks = [dict(row) for row in connection.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT 100")]
            approvals = [dict(row) for row in connection.execute("SELECT * FROM approvals ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,rowid DESC LIMIT 100")]
            task_ids = {item["id"] for item in tasks}
            for approval in approvals:
                if approval["status"] == "pending" and approval["task_id"] not in task_ids:
                    linked = connection.execute("SELECT * FROM tasks WHERE id=?", (approval["task_id"],)).fetchone()
                    if linked:
                        tasks.append(dict(linked))
                        task_ids.add(linked["id"])
            tasks.sort(key=lambda item: item["created"], reverse=True)
            for item in tasks:
                item["payload"] = json.loads(item["payload"])
                item["created"], item["updated"] = iso(item["created"]), iso(item["updated"])
            events = [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY id DESC LIMIT 100")]
            for item in events:
                item["time"] = iso(item["time"])
            schedules = [dict(row) for row in connection.execute("SELECT * FROM schedules ORDER BY name")]
            for item in schedules:
                item["enabled"] = bool(item["enabled"])
                item["next_run"] = iso(item["next_run"])
                runs = connection.execute("SELECT status,result,error,updated FROM tasks WHERE dedupe_key LIKE ? ORDER BY updated DESC LIMIT 5",
                                          (item["id"] + ":%",)).fetchall()
                item["history"] = [{"status": r["status"], "at": iso(r["updated"]),
                                    "detail": (r["error"] or r["result"] or "")[:300]} for r in runs]
            preferences = (self.git.preferences() if self.git else None) or self.meta(connection, "preferences", {"theme": catalog["system"]["default_theme"], "panels": PANELS})
            # Panels added after you saved your choices start visible; ones you hid stay hidden.
            known = self.meta(connection, "panels_known", None)
            chosen = [p for p in preferences.get("panels", PANELS) if p in PANELS]
            if known is None and set(ORIGINAL_PANELS) <= set(chosen):
                known = ORIGINAL_PANELS  # never customized: show the new panels too
            preferences = {**preferences, "panels": chosen + [p for p in PANELS if p not in (known or PANELS) and p not in chosen]}
            base = {"version": "1.1.0", "brain": {"root": str(self.brain.root), **self.brain.revision(),
                        "reconciled_head": self.meta(connection, "reconciled_head"),
                        "drift": fingerprint != self.meta(connection, "brain_fingerprint")},
                    "apps": apps, "tasks": tasks, "schedules": schedules,
                    "approvals": approvals, "workflows": [{"id": w["id"], "name": w.get("name", w["id"])} for w in catalog["workflows"]],
                    "memories": [dict(row) for row in connection.execute("SELECT * FROM memories ORDER BY updated DESC LIMIT 100")],
                    "events": events, "adapters": adapters.statuses(), "themes": catalog["themes"],
                    "preferences": preferences, "panel_ids": PANELS,
                    "storage": "git" if self.git else "private",
                    "capabilities": self.capabilities()}
        # Module apps and workspace readers run outside the transaction: an app may log (its own connection).
        base["modules"] = self._modules_snapshot()
        base["chats"] = self._chats_list()
        base["models"] = self._cached("models", lambda: __import__("aihq.llm", fromlist=["llm"]).catalog(self.brain.root.parent, probe_ollama=False))
        def _activity():
            act = __import__("aihq.activity", fromlist=["activity"])
            domain = self.brain.root.parent
            return [{**entry, "sentence": act.sentence(entry, domain)} for entry in act.recent(domain)[:50]]
        base["activity"] = self._guarded(_activity)
        base["connectors"] = self._cached("connectors", self._connectors_list)
        base["telegram"] = self._cached("telegram", self._telegram_status, default={})
        base.update(self.workspace(catalog, tasks, schedules))
        return base

    def _cached(self, key, reader, default=None, seconds=30):
        """Sections that ask the Keychain or start MCP servers: computed at most every 30 s."""
        cache = self.__dict__.setdefault("_section_cache", {})
        hit = cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
        value = self._guarded(reader, default)
        cache[key] = (time.time(), value)
        return value

    @staticmethod
    def _guarded(reader, default=None):
        """A snapshot section whose module may not be installed yet, or may fail: never fatal."""
        try:
            value = reader()
            return value if value is not None else ([] if default is None else default)
        except Exception:
            return [] if default is None else default

    def _connectors_list(self):
        from . import connectors as conn
        out = []
        for cid, connector in conn.registry(self.brain.root.parent).items():
            try:
                status = connector.status()
            except Exception as exc:
                status = {"ok": False, "detail": str(exc)[:200]}
            try:
                count = len(connector.tools()) if status.get("ok") else 0
            except Exception:
                count = 0
            out.append({"id": cid, "name": getattr(connector, "name", cid), "kind": getattr(connector, "kind", "builtin"),
                        "description": getattr(connector, "description", ""), "status": status, "tools": count,
                        "enabled": bool(getattr(connector, "enabled", True))})
        return out

    def _telegram_status(self):
        from . import telegram_gateway as tg
        cfg = tg.config(self.brain.root.parent)
        return {"token": bool(tg.token()), "running": tg.running(), "chat": bool(cfg.get("chat_id")),
                "allow": [str(x) for x in cfg.get("allow") or []]}

    def start_gateway(self) -> bool:
        """Telegram long-poll in its own daemon thread, when the bot token exists."""
        try:
            from . import telegram_gateway as tg
        except Exception:
            return False
        def log(what):
            try:
                with self.connect() as connection:
                    self.event(connection, "telegram", what[:400])
            except Exception:
                pass
        return tg.start(self, log=log)

    def inbox_decide(self, item_id: str, status: str, note: str = "") -> dict:
        """Approve or reject an inbox item wherever it came from: a queued connector call runs
        once here; app-owned items go back to their own apps."""
        if status not in {"approved", "rejected", "done", "open"}:
            raise ValueError("Status is approved, rejected, done or open.")
        if not re_inbox_id.fullmatch(item_id):
            raise ValueError("Unknown inbox item.")
        domain = self.brain.root.parent
        path = domain / "hq" / "os" / "inbox" / f"{item_id}.md"
        if not path.is_file():
            raise ValueError("Inbox item not found.")
        meta, body = agentapps.frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("status") != "open" and status != "open":
            return {"ok": True, "message": f"already {meta.get('status')}", "item": meta}
        if meta.get("action") == "connector.call":
            from . import connectors as conn
            if status == "approved":
                try:
                    result = conn.execute_approved(domain, {**meta, "body": body})
                except Exception as exc:
                    _write_inbox_status(path, meta, body, "open", f"Approved, but it failed: {str(exc)[:300]}")
                    raise ValueError(f"It failed: {exc}")
                summary = (result or {}).get("summary") or (result or {}).get("result") or "done"
                _write_inbox_status(path, meta, body, "done", f"Approved by {core.owner_name()}{(' (' + note + ')') if note else ''}. {str(summary)[:300]}")
                message = f"Done: {str(summary)[:200]}"
            else:
                _write_inbox_status(path, meta, body, status, note or f"{status.capitalize()} by {core.owner_name()}.")
                message = status
            with self.connect() as connection:
                self.event(connection, "inbox.set", f"{meta.get('title', item_id)}: {message}")
            return {"ok": True, "message": message}
        app_id = str(meta.get("app") or core.local_config().get("inbox_app") or "")
        if not app_id:
            raise ValueError("This inbox item names no owning app.")
        app = modules.get(self.brain, app_id, self)
        # Apps speak one of two decide shapes: the standard inbox.set, or approve/reject verbs.
        result = modules.safe_call(app, "action", {"action": "inbox.set", "id": item_id, "status": status, "note": note},
                                   default={"error": f"{app_id} cannot decide inbox items."})
        if isinstance(result, dict) and "error" in result and len(result) == 1 and status in {"approved", "rejected"}:
            result = modules.safe_call(app, "action",
                                       {"action": "approve" if status == "approved" else "reject", "id": item_id, "note": note},
                                       default=result)
        if isinstance(result, dict) and "error" in result and len(result) == 1:
            raise ValueError(result["error"])
        return result if isinstance(result, dict) else {"ok": True}

    def _chats_list(self, app=None):
        try:
            threads = agentapps._Chats(agentapps._deps(None).get("chats"), self.brain.root.parent)
            items = threads.list(app=app)
            return items[:50] if isinstance(items, list) else []
        except Exception:
            return []

    def _modules_snapshot(self):
        try:
            return modules.snapshot(self.brain, self)
        except Exception as exc:  # the catalog is already validated; this guards the apps themselves
            return {"error": str(exc)[:300]}

    def workspace(self, catalog, tasks, schedules=None):
        """Agents, skills, Git and the Infinite Arch views. A reader that fails reports itself; the rest still load."""
        out = {}
        readers = {"agents": lambda: agentops.agents(catalog, tasks), "agent_proposals": lambda: agentops.proposals(self.brain.root),
                   "skills": lambda: agentops.skills(tasks), "git": lambda: agentops.git_status(self.brain.root),
                   "availability": agentops.availability}
        for key, reader in readers.items():
            try:
                out[key] = reader()
            except Exception as exc:  # one broken reader must not take the dashboard down
                out[key] = {"error": str(exc)[:300]}
        try:
            extra = modules.agents(self.brain, self)
            if isinstance(out.get("agents"), list):
                out["agents"].extend(extra)
        except Exception as exc:
            out["module_agents_error"] = str(exc)[:300]
        out["views"] = {}
        for name in ("today", "recos", "routines", "usage", "memory"):
            cards, kpis, _ = arms.domain(name)
            out["views"][name] = {"cards": cards, "kpis": kpis}
        try:
            self._module_cards(out["views"], tasks, schedules or [])
        except Exception as exc:
            out["module_views_error"] = str(exc)[:300]
        return out

    def _module_cards(self, views, tasks, schedule_rows):
        """Module routines and usage next to the OS's own, in the same card shape."""
        schedules = {row["workflow"]: row for row in schedule_rows}
        routine_cards = []
        for routine in modules.routines(self.brain, self):
            schedule = schedules.get(routine["workflow"])
            runs = [t for t in tasks if t["operation"] == "module.routine"
                    and t["payload"].get("app") == routine["app"] and t["payload"].get("routine") == routine["id"]]
            last = runs[0] if runs else None
            cadence = routine.get("every") or (("daily at " + routine["at"]) if routine.get("at") else "on demand")
            fields = [["App", routine["app"]], ["Cadence", cadence],
                      ["Schedule", ("enabled" if schedule["enabled"] else "paused") if schedule else "none"],
                      ["Next run", schedule["next_run"] if schedule and schedule["enabled"] else "not scheduled"],
                      ["Last run", f"{last['status']} at {last['updated']}" if last else "never"],
                      ["Mode", "active" if routine.get("agent") else "passive"]]
            card = arms._card(f"mod-{routine['app']}-{routine['id']}", f"{routine['app']}: {routine['name']}",
                              "enabled" if schedule and schedule["enabled"] else ("scheduled" if schedule else "idle"),
                              f"Module routine {routine['workflow']}.", fields)
            card["module"] = {"app": routine["app"], "routine": routine["id"]}
            card["actions"] = ["run"] + card["actions"]
            routine_cards.append(card)
        if routine_cards:
            views.setdefault("routines", {"cards": [], "kpis": []})
            views["routines"]["cards"] = routine_cards + views["routines"]["cards"]
        usage_cards = []
        for app_id, use in modules.usage(self.brain, self).items():
            if "error" in use:
                usage_cards.append(arms._card(f"use-{app_id}", f"{app_id} usage", "error", use["error"], []))
                continue
            by_agent = use.get("by_agent") or {}
            fields = [["Today", arms._n(int(use.get("today", 0) or 0))], ["Last 7 days", arms._n(int(use.get("7d", 0) or 0))],
                      ["Last 30 days", arms._n(int(use.get("30d", 0) or 0))], ["Runs 30d", str(use.get("runs_30d", 0))]]
            fields += [[f"Agent {name}", arms._n(int(value or 0))] for name, value in list(by_agent.items())[:8]]
            usage_cards.append(arms._card(f"use-{app_id}", f"{app_id}: model tokens", "ok",
                                          f"Tokens the {app_id} app spent on its own model calls.", fields))
        if usage_cards:
            views.setdefault("usage", {"cards": [], "kpis": []})
            views["usage"]["cards"] = usage_cards + views["usage"]["cards"]

    @staticmethod
    def capabilities():
        return [
            {"name": "Local operations", "status": "working", "detail": "Persistent tasks, memory, records, activity and approvals in SQLite."},
            {"name": "Apps", "status": "working", "detail": "Module apps are code under apps/<id> (system/contracts/MODULES.md); the loaded set is reconciled from the catalog. No builder, no templates."},
            {"name": "Automation", "status": "working", "detail": "Interval schedules and atomic local digest jobs while the worker is running; missed intervals coalesce."},
            {"name": "Agent execution", "status": "working-bounded", "detail": "Hermes, Codex, Claude and local Ollama run after per-request approval; the dashboard starts its worker. Edit mode (Claude, Codex) works in a Git worktree on its own agent/ branch, committed locally, never pushed. Not an OS sandbox."},
            {"name": "Devin", "status": "handoff-only", "detail": "Portable packets; no remote API integration."},
            {"name": "GitOps", "status": "working", "detail": "Validate/reconcile local declarations. Explicit CLI fast-forward pull; no automatic push, merge or deploy."},
            {"name": "Mac mini service", "status": "documented-unverified", "detail": "Reviewed launchd example; not provisioned or live-tested on a second machine."},
        ]

    def _require_reconciled(self, connection):
        if self.meta(connection, "brain_fingerprint") != self.brain.fingerprint():
            raise ValueError("Brain definitions changed. Validate and reconcile before execution.")

    def _memory(self, connection, key, content):
        key, content = text(key, 120), text(content)
        now = time.time()
        connection.execute("INSERT INTO memories VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,updated=excluded.updated",
                           (new_id(), key, content, now))
        if getattr(self, "git", None):
            self.git.write_memory(key, content, now)
        return {"key": key, "content": content}

    def _task(self, connection, title, operation, payload, dedupe_key=None):
        title = text(title, 200)
        if operation not in {"digest.create", "memory.put", "agent.prompt", "agents.check",
                             "module.routine", "module.ask", "brain.sync"} or not isinstance(payload, dict):
            raise ValueError("Unsupported task operation or payload.")
        if operation == "memory.put":
            payload = {"key": text(payload.get("key"), 120), "content": text(payload.get("content"))}
        elif operation == "agent.prompt":
            if payload.get("adapter") not in adapters.IDS:
                raise ValueError("Unknown agent adapter.")
            if payload["adapter"] == "devin":
                raise ValueError("Devin supports handoff export only. Use ./hq handoff.")
            mode, agent = payload.get("mode", "read"), payload.get("agent")
            if mode not in {"read", "edit"}:
                raise ValueError("Mode must be read or edit.")
            if mode == "edit" and payload["adapter"] not in adapters.EDIT_ADAPTERS:
                raise ValueError("Only Claude and Codex can edit files.")
            payload = {"adapter": payload["adapter"], "prompt": text(payload.get("prompt")),
                       "model": text(payload.get("model", "qwen3:8b"), 120)}
            # Optional fields stay out of the payload unless used, so older approvals keep their binding.
            if mode == "edit":
                payload["mode"] = "edit"
            if agent:
                payload["agent"] = identifier(agent)
        elif operation == "module.routine":
            app, routine = identifier(payload.get("app")), identifier(payload.get("routine"))
            known = {(r["app"], r["id"]) for r in modules.routines(self.brain, self)}
            if (app, routine) not in known:
                raise ValueError(f"Unknown module routine: {app}:{routine}")
            payload = {"app": app, "routine": routine}
        elif operation == "module.ask":
            app = identifier(payload.get("app"))
            modules.get(self.brain, app, self)
            agent = payload.get("agent")
            payload = {"app": app, "text": text(payload.get("text")), "agent": identifier(agent) if agent else None}
        else:
            if payload:
                raise ValueError("Digest, agent check and brain sync tasks take no payload.")
        task_id = new_id()
        status = "awaiting_approval" if operation == "agent.prompt" else "ready"
        connection.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (task_id, title, operation, json.dumps(payload), status, None, None, time.time(), time.time(), dedupe_key))
        if operation == "agent.prompt":
            connection.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?)",
                               (new_id(), task_id, title, "pending", payload["adapter"], adapters.binding(payload, self.brain)))
        self.event(connection, "task_created", "Queued: " + title)
        return {"id": task_id, "status": status}

    def action(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Expected an action object.")
        action = payload.get("action")
        if action == "reconcile":
            return self.reconcile()
        if action == "worker.tick":
            return self.tick()
        if action in {"module.action", "module.ask", "module.routine"}:
            app_id = identifier(payload.get("app"))
            app = modules.get(self.brain, app_id, self)
            if action == "module.action":
                inner = payload.get("payload")
                if not isinstance(inner, dict):
                    raise ValueError("module.action needs a payload object.")
                result = modules.safe_call(app, "action", inner, default={"error": "This app has no actions."})
            elif action == "module.ask":
                if getattr(app, "ask", None) is None:
                    raise ValueError("This app does not answer questions.")
                agent = payload.get("agent") or None
                result = modules.safe_call(app, "ask", text(payload.get("text")), identifier(agent) if agent else None)
            else:
                routine = identifier(payload.get("routine"))
                result = modules.safe_call(app, "run_routine", routine, default={"error": "This app has no routines."})
                with self.connect() as connection:
                    self.event(connection, "module.routine", f"{app_id}:{routine} ran now: " + modules.describe(result)[:200])
            if isinstance(result, dict) and "error" in result and len(result) == 1:
                raise ValueError(result["error"])
            return result
        if action == "chat.send":
            return self.chat_send(payload)
        if action == "inbox.set":
            return self.inbox_decide(str(payload.get("id") or ""), str(payload.get("status") or ""), str(payload.get("note") or ""))
        if action == "models.save":
            from . import llm
            return {"ok": True, "refs": llm.save_model(str(payload.get("ref") or ""), self.brain.root.parent)}
        if action in {"apps.create", "apps.update", "apps.delete", "apps.run", "apps.list"}:
            return self._apps_action(action, payload)
        if action == "chats.list":
            return {"chats": self._chats_list(payload.get("app") or None)}
        if action == "chats.get":
            thread = agentapps._Chats(agentapps._deps(None).get("chats"), self.brain.root.parent).get(str(payload.get("id") or ""))
            if not thread:
                raise ValueError("Thread not found.")
            return thread
        if action == "git.diff":
            return agentops.branch_diff(self.brain.root, payload.get("branch"))
        if action in {"git.merge", "git.discard"}:
            result = (agentops.branch_merge if action == "git.merge" else agentops.branch_discard)(self.brain.root, payload.get("branch"))
            if action == "git.merge":
                self.reconcile()  # merged files may include agent or skill definitions
            with self.connect() as connection:
                self.event(connection, action, result["message"])
            return result
        if action in {"agent.propose", "agent.accept", "skill.create"}:
            if action == "agent.propose":
                result = agentops.propose(self.brain.root, payload)
                message = "Agent proposed at " + result["file"] + ". Accept it to add it, then commit."
            elif action == "agent.accept":
                result = agentops.accept(self.brain.root, payload.get("id"))
                self.reconcile()
                message = "Agent added at " + result["file"] + ". Commit it when you are happy."
            else:
                result = agentops.create_skill(self.brain.root, payload)
                self.reconcile()
                message = "Skill written to " + result["file"] + ". Commit it when you are happy."
            with self.connect() as connection:
                self.event(connection, action, message)
            return {**result, "message": message}
        if isinstance(action, str) and action.startswith("arms."):
            result = arms.act({**payload, "action": action[5:]})
            with self.connect() as connection:
                self.event(connection, action, result["message"])
            return result
        with self.connect() as connection:
            if action == "memory.put":
                result = self._memory(connection, payload.get("key"), payload.get("content"))
                self.event(connection, "memory_saved", "Private memory updated.")
                return result
            if action == "task.create":
                return self._task(connection, payload.get("title"), payload.get("operation"), payload.get("payload", {}))
            if action == "task.cancel":
                row = connection.execute("SELECT status FROM tasks WHERE id=?", (payload.get("id"),)).fetchone()
                if row is None or row["status"] not in {"ready", "awaiting_approval"}:
                    raise ValueError("Only queued tasks can be canceled; running agents must be stopped in their local worker.")
                connection.execute("UPDATE tasks SET status='canceled',updated=? WHERE id=?", (time.time(), payload["id"]))
                connection.execute("UPDATE approvals SET status='rejected' WHERE task_id=? AND status='pending'", (payload["id"],))
            elif action == "approval.decide":
                decision = payload.get("decision")
                if decision not in {"approved", "rejected"}:
                    raise ValueError("Choose approved or rejected.")
                row = connection.execute("SELECT * FROM approvals WHERE id=?", (payload.get("id"),)).fetchone()
                if row is None or row["status"] != "pending":
                    raise ValueError("Pending approval not found.")
                task = connection.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
                if task["status"] != "awaiting_approval":
                    raise ValueError("Task is no longer awaiting approval.")
                if decision == "approved" and row["prompt_hash"] != adapters.binding(json.loads(task["payload"]), self.brain):
                    raise ValueError("Approval context changed. Cancel and submit a new task.")
                connection.execute("UPDATE approvals SET status=? WHERE id=?", (decision, row["id"]))
                connection.execute("UPDATE tasks SET status=?,updated=? WHERE id=?",
                                   ("ready" if decision == "approved" else "canceled", time.time(), row["task_id"]))
            elif action == "schedule.create":
                self._require_reconciled(connection)
                interval = payload.get("interval_seconds")
                if type(interval) is not int or not 60 <= interval <= 31536000:
                    raise ValueError("Schedule interval must be between 60 seconds and one year.")
                workflow = payload.get("workflow")
                if workflow not in {item["id"] for item in self.brain.catalog()["workflows"]}:
                    raise ValueError("Unknown workflow.")
                schedule_id = new_id()
                connection.execute("INSERT INTO schedules VALUES (?,?,?,?,?,?)", (schedule_id, text(payload.get("name"), 160), workflow, interval, 1, time.time() + interval))
                self.event(connection, action, "Local schedule created; requires a running worker.")
                return {"id": schedule_id}
            elif action == "schedule.run":
                count = connection.execute("UPDATE schedules SET next_run=? WHERE id=? AND enabled=1", (time.time(), payload.get("id"))).rowcount
                if not count:
                    raise ValueError("Enable the schedule before running it.")
                self.event(connection, action, "Schedule due now; the worker runs it within a few seconds.")
                return {"ok": True}
            elif action == "schedule.toggle":
                if type(payload.get("enabled")) is not bool:
                    raise ValueError("Enabled must be true or false.")
                count = connection.execute("UPDATE schedules SET enabled=?,next_run=? WHERE id=?", (int(payload["enabled"]), time.time(), payload.get("id"))).rowcount
                if not count:
                    raise ValueError("Schedule not found.")
            elif action == "preferences.set":
                theme, panels = payload.get("theme"), payload.get("panels")
                if theme not in {item["id"] for item in self.brain.catalog()["themes"]}:
                    raise ValueError("Unknown theme.")
                if not isinstance(panels, list) or any(not isinstance(panel, str) or panel not in PANELS for panel in panels) or len(panels) != len(set(panels)):
                    raise ValueError("Unknown dashboard panels.")
                self.set_meta(connection, "preferences", {"theme": theme, "panels": panels})
                self.set_meta(connection, "panels_known", PANELS)
                if self.git:
                    self.git.set_preferences({"theme": theme, "panels": panels})
            elif action == "task.recover":
                count = connection.execute("UPDATE tasks SET status='failed',error='Interrupted agent: outcome unknown. Inspect provider before resubmitting.',updated=? WHERE id=? AND status='running'", (time.time(), payload.get("id"))).rowcount
                if not count:
                    raise ValueError("Running task not found.")
            else:
                raise ValueError("Unknown action.")
            self.event(connection, action, "Recorded local action: " + str(payload.get("id", "preferences")))
            return {"ok": True}

    # ----- conversations and agent apps -----

    @property
    def domain(self):
        return self.brain.root.parent

    def _apps_action(self, action, payload):
        domain = self.domain
        if action == "apps.list":
            return {"apps": [a.manifest() | {"records": len(a.app.records())} for a in agentapps.adapters(domain, self).values()]}
        if action == "apps.create":
            app = agentapps.create(domain, payload.get("spec") or {}, payload.get("prompt") or "")
            self.reconcile()
            with self.connect() as connection:
                self.event(connection, "app.create", f"Agent app created: {app.id}")
            return {"ok": True, "id": app.id, "manifest": agentapps.AgentAppAdapter(app, self).manifest()}
        app_id = identifier(payload.get("id"))
        if action == "apps.update":
            app = agentapps.update(domain, app_id, payload.get("spec"), payload.get("prompt"))
            self.reconcile()
            return {"ok": True, "id": app.id, "spec": app.spec}
        if action == "apps.delete":
            target = agentapps.delete(domain, app_id)
            self.reconcile()
            with self.connect() as connection:
                self.event(connection, "app.delete", f"Agent app retired: {app_id}")
            return {"ok": True, "moved_to": str(target.relative_to(domain))}
        result = agentapps.run(domain, app_id, runtime=self)
        with self.connect() as connection:
            self.event(connection, "app.run", f"{app_id} ran now: " + modules.describe(result)[:200])
        return result

    def chat_send(self, payload):
        """One turn in a thread. app set: that agent app. agent set or nothing: a module app's chat
        when one offers it, else the OS Operator, who can create apps."""
        text_in = str(payload.get("text") or "").strip()
        images = payload.get("images") or []
        if not isinstance(images, list):
            raise ValueError("images must be a list.")
        if not text_in and not images:
            raise ValueError("Say something, or attach a photo.")
        if len(text_in) > 20000:
            raise ValueError("Keep a message under 20,000 characters.")
        thread_id = str(payload.get("thread") or "") or None
        app = payload.get("app") or None
        agent = payload.get("agent") or None
        model_ref = str(payload.get("model") or "").strip() or None
        chosen = None
        if model_ref and model_ref not in ("default", "auto"):
            from . import llm
            chosen = llm.resolve(model_ref)
        if app:
            app_id = identifier(app)
            result = agentapps.chat(self.domain, app_id, thread_id, text_in, images=images, runtime=self, model=chosen)
            return self._chat_reply(result["thread"], result["answer"], result.get("run"), result.get("tool_calls", []),
                                    result.get("usage", {}), f"app:{app_id}")
        if agent is None and thread_id:
            try:
                existing = agentapps._Chats(agentapps._deps(None).get("chats"), self.domain).get(thread_id) or {}
                owner = str(existing.get("agent") or "")
                if owner.startswith("app:") and existing.get("app"):
                    return self.chat_send({**payload, "app": existing["app"]})
                agent = "operator" if owner == "operator" else (owner or None)
            except Exception:
                agent = None
        if agent != "operator" and chosen is None:  # module apps pick their own models; a chosen model goes to the Operator
            chat_apps = sorted(aid for aid, a in modules.load(self.brain, self).items()
                               if not isinstance(a, dict) and hasattr(a, "chat"))
            if chat_apps:
                chat_app = modules.get(self.brain, chat_apps[0], self)
                result = chat_app.chat(thread_id, text_in, identifier(agent) if agent and agent != "auto" else None, images)
                if isinstance(result, dict) and "error" in result and len(result) == 1:
                    raise ValueError(result["error"])
                if isinstance(result, dict):
                    return self._chat_reply(result.get("thread", thread_id), result.get("answer", ""), result.get("run"),
                                            result.get("tool_calls", []), result.get("usage", {}),
                                            result.get("agent", agent or chat_apps[0]))
            elif agent and agent != "auto":
                raise ValueError(f"No agent named {agent}; no module app offers chat.")
        return self._os_operator(thread_id, text_in, images, model=chosen)

    OPERATOR_PROMPT = """# Operator of the InfiniteArch Agentic OS

I run {owner}'s Agentic OS: the dashboard, the apps (module apps under apps/<id>, and agent apps
made from a prompt and a schedule), the routines, the inbox and the memory. GitHub is the brain:
everything durable is a file under hq/.

What I do here:
- Answer from memory, the HQ and the apps. If a question belongs to an installed app, I say so
  and point there.
- Make apps when asked. "Find art shows daily" becomes create_app with a clear prompt written for
  the model that will run it, sensible fields (always include a url or other key), a cadence, and
  web on. I confirm what I made in two lines. I update or retire apps the same way.
- Make bots, not just boards. An app can have triggers (schedule every/at, an email search
  that runs it when new mail matches, a Telegram command like /scout), connectors (gmail,
  calendar, telegram, instagram, site, mcp.<server>) and a policy per action: "auto" just does
  it, "ask" queues it for the owner. Default every outside action to ask unless the owner says
  "just do it". Print, Photos and canon always ask.
- Use the connectors myself when asked ("put that on my calendar", "draft a reply"): reads
  run, actions queue for approval and I say so.
- Run an app now when asked (run_app) and report what changed.

Voice: direct, first person, verdict first, short. No em dashes, ever. Never flatter. When I do
not know, I say so and say how to find out.
"""

    def _os_operator(self, thread_id, text_in, images, model=None):
        domain = self.domain
        deps = agentapps._deps(None)
        threads = agentapps._Chats(deps.get("chats"), domain)
        if not thread_id:
            thread = threads.create(text_in[:60] or "OS chat", "operator", app=None)
            thread_id = agentapps._thread_id(thread)
        threads.append(thread_id, "user", text_in, images=images)
        history = threads.history(thread_id)
        if not isinstance(history, list) or not history:
            history = [{"role": "user", "content": text_in}]
        system = self.OPERATOR_PROMPT.format(owner=core.owner_name())
        context = str(core.local_config().get("operator_context") or "").strip()
        if context:
            system += "\n\n" + context
        system += "\n\nToday is " + datetime.now().strftime("%A %Y-%m-%d") + ".\n\n"
        memory = agentapps.memory_digest(domain)
        if memory:
            system += "## What the OS knows\n" + memory + "\n\n"
        apps_now = [f"- {a.id}: {a.spec['name']} ({a.spec['every'] or a.spec['at'] or 'on demand'}, {len(a.records())} records)"
                    for a in agentapps.load(domain).values()]
        system += "## Agent apps now\n" + ("\n".join(apps_now) if apps_now else "none yet")
        base = agentapps._base_toolbelt(deps, domain, None, self, with_app_ops=True)
        belts = [base] if base is not None else [agentapps.AppOpsTools(domain, self)]
        try:  # the Operator may use every connector; every outside action still asks the owner first
            from . import connectors as conn
            belts.append(conn.ConnectorTools(domain, "all", {"*": "ask"}, "operator", self))
        except Exception:
            pass
        belt = agentapps.Composite(belts)
        model = agentapps._pick_model(deps, "default", True, model)
        started = datetime.now()
        result = agentapps._loop(deps, model, system, history, belt)
        answer = result.get("answer", "")
        usage = result.get("usage") or {}
        threads.append(thread_id, "assistant", answer, tool_calls=result.get("tool_calls", []), usage=usage)
        run_id = f"{started:%H%M%S}-{uuid.uuid4().hex[:8]}"
        agentapps.save_run(domain, {"id": run_id, "agent": "operator", "thread": thread_id, "prompt": text_in, "answer": answer,
                                    "tool_calls": result.get("tool_calls", []),
                                    "usage": {"input": int(usage.get("input", usage.get("input_tokens", 0)) or 0),
                                              "output": int(usage.get("output", usage.get("output_tokens", 0)) or 0)},
                                    "started": started.isoformat(timespec="seconds"), "finished": datetime.now().isoformat(timespec="seconds")})
        if threads.needs_summary(thread_id):
            try:
                summary = agentapps._loop(deps, model, "Summarize this conversation in three plain lines for someone resuming it later. No em dashes.",
                                          history + [{"role": "assistant", "content": answer}, {"role": "user", "content": "Summarize."}],
                                          agentapps.Composite([]), max_steps=1)
                threads.set_summary(thread_id, summary.get("answer", ""))
            except Exception:
                pass
        agentapps.log_line(domain, "operator", f"chat: {text_in[:80]}", f"hq/os/runs/{started:%Y-%m-%d}/{run_id}.json")
        return self._chat_reply(thread_id, answer, run_id, result.get("tool_calls", []), usage, "operator")

    @staticmethod
    def _chat_reply(thread_id, answer, run_id, tool_calls, usage, agent):
        """The shape the shell's chat component renders: message is an object, not a string."""
        return {"thread": thread_id, "agent": agent, "run": run_id,
                "message": {"role": "assistant", "who": agent, "time": datetime.now().isoformat(timespec="seconds"),
                            "text": answer, "tool_calls": tool_calls, "usage": usage},
                "tool_calls": tool_calls, "usage": usage}

    def tick(self, allow_external=False, now=None, max_tasks=20):
        now = time.time() if now is None else now
        catalog = self.brain.catalog()
        workflows = {item["id"]: item for item in catalog["workflows"]}
        with self.connect() as connection:
            self._require_reconciled(connection)
            for schedule in connection.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=?", (now,)).fetchall():
                workflow = workflows.get(schedule["workflow"])
                parsed = modules.parse_workflow(schedule["workflow"])
                if workflow is None and parsed is None:
                    raise ValueError("Schedule workflow disappeared; reconcile first.")
                if workflow is None:
                    self._task(connection, schedule["name"], "module.routine", {"app": parsed[0], "routine": parsed[1]}, f"{schedule['id']}:{schedule['next_run']}")
                else:
                    self._task(connection, schedule["name"], workflow["operation"], workflow["payload"], f"{schedule['id']}:{schedule['next_run']}")
                connection.execute("UPDATE schedules SET next_run=? WHERE id=?", (now + schedule["interval_seconds"], schedule["id"]))
        processed = []
        for _ in range(max_tasks):
            external = None
            local = None
            with self.connect() as connection:
                self._require_reconciled(connection)
                query = "SELECT * FROM tasks WHERE status='ready'"
                if not allow_external:
                    query += " AND operation!='agent.prompt'"
                task = connection.execute(query + " ORDER BY created LIMIT 1").fetchone()
                if task is None:
                    break
                payload = json.loads(task["payload"])
                if task["operation"] == "agent.prompt":
                    approval = connection.execute("SELECT * FROM approvals WHERE task_id=?", (task["id"],)).fetchone()
                    if approval is None or approval["status"] != "approved" or approval["prompt_hash"] != adapters.binding(payload, self.brain):
                        connection.execute("UPDATE tasks SET status='failed',error='Approval is missing or stale',updated=? WHERE id=?", (time.time(), task["id"]))
                        self.event(connection, "approval_stale", "Agent request blocked: " + task["title"])
                        processed.append(task["id"])
                        continue
                    connection.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (time.time(), task["id"]))
                    self.event(connection, "agent_started", "Approved local agent started: " + task["title"])
                    external = {**dict(task), "approved_binding": approval["prompt_hash"]}
                else:
                    if task["operation"] == "memory.put":
                        result = self._memory(connection, payload["key"], payload["content"])
                    elif task["operation"] == "agents.check":
                        found = agentops.availability()
                        result = self._memory(connection, "agent-availability", "\n".join(
                            f"{item['adapter']}: {item['status']} ({item['detail']}) checked {item['checked']}" for item in found))
                    elif task["operation"] in {"module.routine", "module.ask", "brain.sync"}:
                        connection.execute("UPDATE tasks SET status='running',updated=? WHERE id=?", (time.time(), task["id"]))
                        local = dict(task)
                        result = None
                    elif task["operation"] == "digest.create":
                        memories = connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                        ready = connection.execute("SELECT COUNT(*) FROM tasks WHERE status='ready'").fetchone()[0]
                        result = self._memory(connection, "latest-digest", f"Local digest: {memories} memories; {ready} queued tasks. Generated {iso(now)}.")
                    else:
                        raise ValueError("Unsupported task operation in database.")
                    if local is None:
                        connection.execute("UPDATE tasks SET status='succeeded',result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), task["id"]))
                        self.event(connection, "task_succeeded", "Completed: " + task["title"])
            if local:
                try:
                    if local["operation"] == "brain.sync":
                        if not getattr(self, "git", None):
                            raise ValueError("Brain sync needs Git-brain mode (os/curator.json).")
                        result = gitsync.sync(self.git.domain)
                        if result.get("error"):
                            raise ValueError(result["error"])
                    elif local["operation"] == "module.routine":
                        app = modules.get(self.brain, payload["app"], self)
                        result = modules.safe_call(app, "run_routine", payload["routine"], default={"error": "This app has no routines."})
                        if isinstance(result, dict) and set(result) == {"error"}:
                            raise ValueError(result["error"])
                        if self.git:
                            self.rebuild_from_git()  # the routine may have written memory or records
                    elif local["operation"] == "module.ask":
                        app = modules.get(self.brain, payload["app"], self)
                        result = modules.safe_call(app, "ask", payload["text"], payload.get("agent"))
                        if isinstance(result, dict) and set(result) == {"error"}:
                            raise ValueError(result["error"])
                    else:
                        raise ValueError("Unsupported local operation " + local["operation"])
                    status, error = "succeeded", None
                except Exception as exception:
                    result, status, error = None, "failed", str(exception)[:10000]
                with self.connect() as connection:
                    connection.execute("UPDATE tasks SET status=?,result=?,error=?,updated=? WHERE id=? AND status='running'",
                                       (status, json.dumps(result) if result is not None else None, error, time.time(), local["id"]))
                    self.event(connection, "task_" + status, local["operation"] + ": " + local["title"])
            if external:
                try:
                    if payload.get("mode") == "edit":
                        # Edits happen in a fresh worktree on an agent/... branch, never in your checkout.
                        tree = agentops.worktree(self.brain.root, self.home, external["id"], external["title"])
                        output = adapters.execute(payload, tree["cwd"], timeout=adapters.EDIT_TIMEOUT,
                                                  expected_binding=external["approved_binding"], brain=self.brain)
                        result = agentops.finish_worktree(tree, external["title"]) + "\n\n" + output
                    else:
                        result = adapters.execute(payload, self.brain.root, expected_binding=external["approved_binding"], brain=self.brain)
                    status, error = "succeeded", None
                except Exception as exception:
                    result, status, error = None, "failed", str(exception)[:10000]
                with self.connect() as connection:
                    connection.execute("UPDATE tasks SET status=?,result=?,error=?,updated=? WHERE id=? AND status='running'", (status, result, error, time.time(), external["id"]))
                    self.event(connection, "task_" + status, "Agent run " + status + ": " + external["title"])
            processed.append(task["id"])
        return {"processed": processed, "external_enabled": allow_external}

    def handoff(self, task_id=None):
        task = None
        if task_id:
            with self.connect() as connection:
                row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if row is None:
                    raise ValueError("Task not found.")
                task = {"title": row["title"], "operation": row["operation"], "payload": json.loads(row["payload"]), "status": row["status"]}
        return {"schema_version": 1, "brain_revision": self.brain.revision(), "brain_fingerprint": self.brain.fingerprint(),
                "read_first": ["AGENTS.md", "docs/ARCHITECTURE.md", "docs/HANDOFF.md", "system/contracts", "vault/README.md"],
                "acceptance": "Validate definitions, run tests and record evidence; work on a branch, never auto-merge.",
                "permissions": "No approval or credentials transfer with this packet. Reauthorize external actions on the receiving machine.",
                "task": task, "private_data": "Excluded by default. An explicitly selected task includes its prompt; review before sharing."}
