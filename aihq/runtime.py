"""SQLite operational store for local headquarters activity.

Canonical agents, skills, and architecture stay in Git. This database holds
private runtime records and may be deleted to roll back local activity.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from . import core

RUNTIME_DIR = core.ROOT / ".local" / "hq"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  task_id TEXT,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  cadence_seconds INTEGER,
  enabled INTEGER NOT NULL,
  approval_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  next_run_at TEXT,
  last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  schedule_id TEXT,
  agent_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  outcome TEXT NOT NULL,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  status TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decision_note TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  workspace_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  provenance TEXT NOT NULL,
  created_at TEXT NOT NULL,
  ref_id TEXT
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  role TEXT NOT NULL,
  body TEXT NOT NULL,
  provider TEXT,
  locality TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


class StoreError(sqlite3.Error):
    """Raised when the local store cannot be read or written."""


def _connect() -> sqlite3.Connection:
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(RUNTIME_DIR / "hq.sqlite3", timeout=5)
    except sqlite3.Error as exc:
        raise StoreError(str(exc)) from exc
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def ping() -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()


def get_preferences() -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT value FROM preferences WHERE key = 'prefs'").fetchone()
        finally:
            conn.close()
    if row is None:
        return {}
    try:
        loaded = json.loads(row["value"])
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_preferences(prefs: dict[str, Any]) -> None:
    payload = json.dumps(prefs, ensure_ascii=False)
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO preferences(key, value) VALUES('prefs', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (payload,),
            )
            conn.commit()
        finally:
            conn.close()


def insert_task(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO tasks(id, workspace_id, title, detail, status, created_at, updated_at) "
                "VALUES(:id, :workspace_id, :title, :detail, :status, :created_at, :updated_at)",
                record,
            )
            conn.commit()
        finally:
            conn.close()
    return record


def list_tasks(workspace_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def insert_schedule(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO schedules(id, workspace_id, task_id, name, kind, cadence_seconds, enabled, "
                "approval_id, created_at, updated_at, next_run_at, last_run_at) "
                "VALUES(:id, :workspace_id, :task_id, :name, :kind, :cadence_seconds, :enabled, "
                ":approval_id, :created_at, :updated_at, :next_run_at, :last_run_at)",
                record,
            )
            conn.commit()
        finally:
            conn.close()
    return record


def list_schedules(workspace_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE workspace_id = ? ORDER BY created_at DESC",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def due_schedules(now: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM schedules WHERE enabled = 1 AND cadence_seconds IS NOT NULL "
                "AND (next_run_at IS NULL OR next_run_at <= ?) ORDER BY next_run_at",
                (now,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        finally:
            conn.close()
    return _row(row)


def update_schedule_fields(schedule_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"enabled", "updated_at", "next_run_at", "last_run_at", "approval_id"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError("Unsupported schedule update")
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    with _lock:
        conn = _connect()
        try:
            conn.execute(f"UPDATE schedules SET {assignments} WHERE id = :id", {**fields, "id": schedule_id})
            conn.commit()
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        finally:
            conn.close()
    return _row(row)


def insert_run(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO runs(id, workspace_id, schedule_id, agent_id, kind, status, outcome, error, started_at, finished_at) "
                "VALUES(:id, :workspace_id, :schedule_id, :agent_id, :kind, :status, :outcome, :error, :started_at, :finished_at)",
                record,
            )
            conn.commit()
        finally:
            conn.close()
    return record


def list_runs(workspace_id: str, schedule_id: str | None = None, agent_id: str | None = None) -> list[dict[str, Any]]:
    clauses = ["workspace_id = ?"]
    params: list[Any] = [workspace_id]
    if schedule_id:
        clauses.append("schedule_id = ?")
        params.append(schedule_id)
    if agent_id:
        clauses.append("agent_id = ?")
        params.append(agent_id)
    sql = "SELECT * FROM runs WHERE " + " AND ".join(clauses) + " ORDER BY started_at DESC"
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def running_runs(workspace_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND status = 'running' ORDER BY started_at DESC",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def latest_error(workspace_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND error IS NOT NULL AND error != '' "
                "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        finally:
            conn.close()
    return _row(row)


def latest_agent_error(workspace_id: str, agent_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND agent_id = ? AND error IS NOT NULL AND error != '' "
                "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
                (workspace_id, agent_id),
            ).fetchone()
        finally:
            conn.close()
    return _row(row)


def latest_status_run(workspace_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE workspace_id = ? AND kind = 'adapter-status' "
                "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        finally:
            conn.close()
    return _row(row)


def insert_approval(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO approvals(id, workspace_id, subject_type, subject_id, title, detail, status, "
                "requested_at, decided_at, decision_note) "
                "VALUES(:id, :workspace_id, :subject_type, :subject_id, :title, :detail, :status, "
                ":requested_at, :decided_at, :decision_note)",
                record,
            )
            conn.commit()
        finally:
            conn.close()
    return record


def list_approvals(workspace_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM approvals WHERE workspace_id = ? ORDER BY requested_at DESC",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def get_approval(approval_id: str) -> dict[str, Any] | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        finally:
            conn.close()
    return _row(row)


def update_approval(approval_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"status", "decided_at", "decision_note"}
    if set(fields) - allowed:
        raise ValueError("Unsupported approval update")
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    with _lock:
        conn = _connect()
        try:
            conn.execute(f"UPDATE approvals SET {assignments} WHERE id = :id", {**fields, "id": approval_id})
            conn.commit()
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        finally:
            conn.close()
    return _row(row)


def add_event(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "INSERT INTO events(workspace_id, kind, message, provenance, created_at, ref_id) "
                "VALUES(:workspace_id, :kind, :message, :provenance, :created_at, :ref_id)",
                record,
            )
            conn.commit()
            record = {**record, "id": cursor.lastrowid}
        finally:
            conn.close()
    return record


def list_events(workspace_id: str) -> list[dict[str, Any]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT * FROM events WHERE workspace_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
                (workspace_id,),
            ).fetchall()
        finally:
            conn.close()
    return [dict(row) for row in rows]


def ensure_conversation(record: dict[str, Any]) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO conversations(id, workspace_id, created_at) VALUES(:id, :workspace_id, :created_at)",
                record,
            )
            conn.commit()
        finally:
            conn.close()


def add_message(record: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO messages(id, conversation_id, workspace_id, role, body, provider, locality, status, created_at) "
                "VALUES(:id, :conversation_id, :workspace_id, :role, :body, :provider, :locality, :status, :created_at)",
                record,
            )
            conn.commit()
        finally:
            conn.close()
    return record
