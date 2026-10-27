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
from .brain import Brain, QUESTIONS, TEMPLATES, digest, identifier, text
from .storage import private_home

PANELS = ["apps", "tasks", "schedules", "memory", "activity", "system"]
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

    @staticmethod
    def event(connection, event, message):
        connection.execute("INSERT INTO events(time,event,message) VALUES (?,?,?)", (time.time(), event, message))

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
            for row in connection.execute("SELECT id FROM apps").fetchall():
                if row["id"] not in present:
                    connection.execute("UPDATE apps SET status='unavailable' WHERE id=?", (row["id"],))
            for row in connection.execute("SELECT * FROM schedules WHERE enabled=1").fetchall():
                if row["workflow"] not in {item["id"] for item in catalog["workflows"]}:
                    connection.execute("UPDATE schedules SET enabled=0 WHERE id=?", (row["id"],))
            self.set_meta(connection, "brain_fingerprint", fingerprint)
            self.set_meta(connection, "reconciled_head", self.brain.revision()["head"])
            if previous != fingerprint:
                self.event(connection, "brain_reconciled", "Validated versioned declarations and updated local catalog.")
        return {"fingerprint": fingerprint, "apps": len(catalog["apps"]), "changed": previous != fingerprint}

    def snapshot(self):
        catalog = self.brain.catalog()
        fingerprint = self.brain.fingerprint()
        with self.connect() as connection:
            statuses = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM apps")}
            apps = [{**item, "status": statuses.get(item["id"], {}).get("status", "available")} for item in catalog["apps"]]
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
            records = {}
            for row in connection.execute("SELECT * FROM records ORDER BY created DESC LIMIT 1000"):
                item = dict(row)
                item["created"] = iso(item["created"])
                records.setdefault(item.pop("app_id"), []).append(item)
            schedules = [dict(row) for row in connection.execute("SELECT * FROM schedules ORDER BY name")]
            for item in schedules:
                item["enabled"] = bool(item["enabled"])
                item["next_run"] = iso(item["next_run"])
            return {"version": "1.0.0", "brain": {"root": str(self.brain.root), **self.brain.revision(),
                        "reconciled_head": self.meta(connection, "reconciled_head"),
                        "drift": fingerprint != self.meta(connection, "brain_fingerprint")},
                    "apps": apps, "templates": TEMPLATES, "tasks": tasks, "schedules": schedules,
                    "approvals": approvals,
                    "memories": [dict(row) for row in connection.execute("SELECT * FROM memories ORDER BY updated DESC LIMIT 100")],
                    "events": events, "records": records, "adapters": adapters.statuses(), "themes": catalog["themes"],
                    "preferences": self.meta(connection, "preferences", {"theme": catalog["system"]["default_theme"], "panels": PANELS}),
                    "builder_questions": QUESTIONS, "capabilities": self.capabilities()}

    @staticmethod
    def capabilities():
        return [
            {"name": "Local operations", "status": "working", "detail": "Persistent tasks, memory, records, activity and approvals in SQLite."},
            {"name": "Application builder", "status": "working-bounded", "detail": "Interview → preview → approval → runnable declarative record boards; arbitrary code generation requires an external agent."},
            {"name": "Automation", "status": "working", "detail": "Interval schedules and atomic local digest jobs while the worker is running; missed intervals coalesce."},
            {"name": "Agent execution", "status": "requires-configuration", "detail": "Hermes, Codex, Claude and local Ollama adapters need a configured provider, per-request approval and CLI worker --allow-external. Not an OS sandbox."},
            {"name": "Devin", "status": "handoff-only", "detail": "Portable packets; no remote API integration."},
            {"name": "GitOps", "status": "working", "detail": "Validate/reconcile local declarations. Explicit CLI fast-forward pull; no automatic push, merge or deploy."},
            {"name": "Mac mini service", "status": "documented-unverified", "detail": "Reviewed launchd example; not provisioned or live-tested on a second machine."},
        ]

    def _require_reconciled(self, connection):
        if self.meta(connection, "brain_fingerprint") != self.brain.fingerprint():
            raise ValueError("Brain definitions changed. Validate and reconcile before execution.")

    @staticmethod
    def _memory(connection, key, content):
        key, content = text(key, 120), text(content)
        connection.execute("INSERT INTO memories VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET content=excluded.content,updated=excluded.updated",
                           (new_id(), key, content, time.time()))
        return {"key": key, "content": content}

    def _task(self, connection, title, operation, payload, dedupe_key=None):
        title = text(title, 200)
        if operation not in {"digest.create", "memory.put", "agent.prompt"} or not isinstance(payload, dict):
            raise ValueError("Unsupported task operation or payload.")
        if operation == "memory.put":
            payload = {"key": text(payload.get("key"), 120), "content": text(payload.get("content"))}
        elif operation == "agent.prompt":
            if payload.get("adapter") not in adapters.IDS:
                raise ValueError("Unknown agent adapter.")
            if payload["adapter"] == "devin":
                raise ValueError("Devin supports handoff export only. Use ./hq handoff.")
            payload = {"adapter": payload["adapter"], "prompt": text(payload.get("prompt")),
                       "model": text(payload.get("model", "qwen3:8b"), 120)}
        else:
            if payload:
                raise ValueError("Digest tasks take no payload.")
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
        if action == "builder.preview":
            return self.brain.preview(payload)
        if action == "builder.create":
            result = self.brain.build(payload)
            self.reconcile()
            with self.connect() as connection:
                self.event(connection, "app_created", "Approved definition written to Git working tree: " + result["manifest"]["id"])
            return result
        if action == "worker.tick":
            return self.tick()
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
            elif action == "schedule.toggle":
                if type(payload.get("enabled")) is not bool:
                    raise ValueError("Enabled must be true or false.")
                count = connection.execute("UPDATE schedules SET enabled=?,next_run=? WHERE id=?", (int(payload["enabled"]), time.time(), payload.get("id"))).rowcount
                if not count:
                    raise ValueError("Schedule not found.")
            elif action == "app.lifecycle":
                self._require_reconciled(connection)
                app_id = identifier(payload.get("id"))
                row = connection.execute("SELECT * FROM apps WHERE id=?", (app_id,)).fetchone()
                transition = payload.get("transition")
                transitions = {"install": ({"available", "uninstalled"}, "installed"),
                               "enable": ({"installed", "paused"}, "enabled"),
                               "pause": ({"enabled"}, "paused"),
                               "uninstall": ({"installed", "enabled", "paused"}, "uninstalled")}
                if row is None or transition not in transitions or row["status"] not in transitions[transition][0]:
                    raise ValueError("Invalid app lifecycle transition.")
                connection.execute("UPDATE apps SET status=? WHERE id=?", (transitions[transition][1], app_id))
            elif action == "app.record":
                self._require_reconciled(connection)
                app_id = identifier(payload.get("id"))
                row = connection.execute("SELECT status FROM apps WHERE id=?", (app_id,)).fetchone()
                if row is None or row["status"] != "enabled":
                    raise ValueError("Install and enable the app before adding records.")
                note = payload.get("note", "")
                if not isinstance(note, str) or len(note) > 10000:
                    raise ValueError("Note is too long.")
                record_id = new_id()
                connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (record_id, app_id, text(payload.get("title"), 200), text(payload.get("status", "open"), 100), note, time.time()))
                self.event(connection, action, "Private record added to " + app_id)
                return {"id": record_id}
            elif action == "preferences.set":
                theme, panels = payload.get("theme"), payload.get("panels")
                if theme not in {item["id"] for item in self.brain.catalog()["themes"]}:
                    raise ValueError("Unknown theme.")
                if not isinstance(panels, list) or any(not isinstance(panel, str) or panel not in PANELS for panel in panels) or len(panels) != len(set(panels)):
                    raise ValueError("Unknown dashboard panels.")
                self.set_meta(connection, "preferences", {"theme": theme, "panels": panels})
            elif action == "task.recover":
                count = connection.execute("UPDATE tasks SET status='failed',error='Interrupted agent: outcome unknown. Inspect provider before resubmitting.',updated=? WHERE id=? AND status='running'", (time.time(), payload.get("id"))).rowcount
                if not count:
                    raise ValueError("Running task not found.")
            else:
                raise ValueError("Unknown action.")
            self.event(connection, action, "Recorded local action: " + str(payload.get("id", "preferences")))
            return {"ok": True}

    def tick(self, allow_external=False, now=None, max_tasks=20):
        now = time.time() if now is None else now
        catalog = self.brain.catalog()
        workflows = {item["id"]: item for item in catalog["workflows"]}
        with self.connect() as connection:
            self._require_reconciled(connection)
            for schedule in connection.execute("SELECT * FROM schedules WHERE enabled=1 AND next_run<=?", (now,)).fetchall():
                workflow = workflows.get(schedule["workflow"])
                if workflow is None:
                    raise ValueError("Schedule workflow disappeared; reconcile first.")
                self._task(connection, schedule["name"], workflow["operation"], workflow["payload"], f"{schedule['id']}:{schedule['next_run']}")
                connection.execute("UPDATE schedules SET next_run=? WHERE id=?", (now + schedule["interval_seconds"], schedule["id"]))
        processed = []
        for _ in range(max_tasks):
            external = None
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
                    elif task["operation"] == "digest.create":
                        count = connection.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                        ready = connection.execute("SELECT COUNT(*) FROM tasks WHERE status='ready'").fetchone()[0]
                        result = self._memory(connection, "latest-digest", f"Local digest: {count} app records; {ready} queued tasks. Generated {iso(now)}.")
                    else:
                        raise ValueError("Unsupported task operation in database.")
                    connection.execute("UPDATE tasks SET status='succeeded',result=?,updated=? WHERE id=?", (json.dumps(result), time.time(), task["id"]))
                    self.event(connection, "task_succeeded", "Completed: " + task["title"])
            if external:
                try:
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
