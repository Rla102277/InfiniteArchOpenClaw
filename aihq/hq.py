"""Local headquarters services.

Git remains the system definition. This module reads those files, stores operational
records locally, and refuses to push, send mail, or start an adapter from chat text.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Any

from . import core, providers, runtime

PROPOSALS_DIR = core.ROOT / "proposals" / "agents"
SYSTEM_DIR = core.ROOT / "system"
AGENT_DIR = SYSTEM_DIR / "agents"
THEMES = {"hud", "dark", "light"}
KINDS = {"adapter-status", "local-note", "external-action"}
LOCALITY = {"local", "provider-choice"}
_ops = threading.Lock()

DEFAULT_PREFS: dict[str, Any] = {
    "theme": "hud",
    "workspace_id": "local",
    "demo": False,
    "voice_out": False,
    "locality_mode": "local-only",
    "layout": {"domain": None, "selected": None},
}


class HQError(Exception):
    def __init__(self, message: str, status: int = 400, provenance: str = "live") -> None:
        super().__init__(message)
        self.status = status
        self.provenance = provenance


def envelope(data: Any, workspace_id: str, provenance: str, demo: bool) -> dict[str, Any]:
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "provenance": provenance,
        "demo": demo,
        "generated_at": core.timestamp(),
        "data": data,
    }


def fail(message: str, status: int = 400, provenance: str = "live") -> tuple[int, dict[str, Any]]:
    return status, {
        "ok": False,
        "error": message,
        "provenance": provenance,
        "generated_at": core.timestamp(),
    }


def _load_json(path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures() -> dict[str, Any]:
    return _load_json(SYSTEM_DIR / "demo" / "fixtures.json")


def _domains() -> list[dict[str, Any]]:
    return _load_json(SYSTEM_DIR / "domains.json")


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(4)


def _text(value: Any, limit: int, label: str) -> str:
    try:
        return core.clean_text(value, limit)
    except ValueError as exc:
        raise HQError(f"Enter {label} using 1 to {limit} characters.") from exc


def _prefs() -> dict[str, Any]:
    try:
        stored = runtime.get_preferences()
    except sqlite3.Error as exc:
        raise HQError("Preference store is unavailable", 503, "unavailable") from exc
    merged = {**DEFAULT_PREFS, **stored}
    layout = stored.get("layout") if isinstance(stored.get("layout"), dict) else {}
    merged["layout"] = {**DEFAULT_PREFS["layout"], **layout}
    return merged


def _reject_demo(prefs: dict[str, Any]) -> None:
    if prefs.get("demo"):
        raise HQError("Demo mode is read-only. Turn off demo mode to change local records.", 409, "demo")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _plus(value: str, seconds: int) -> str:
    return (_parse_time(value) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def resolve_workspace(requested: str | None, prefs: dict[str, Any], demo: bool) -> str:
    if demo:
        return _fixtures()["workspaces"][0]["id"]
    wid = requested or prefs.get("workspace_id") or "local"
    if not isinstance(wid, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", wid):
        raise HQError("Invalid workspace")
    if wid == "local":
        return wid
    try:
        core.project_path(wid)
    except ValueError as exc:
        raise HQError("Workspace not found", 404) from exc
    return wid


def _workspace_arg(query_workspace: str | None, body: dict[str, Any] | None, prefs: dict[str, Any], demo: bool) -> str:
    body_workspace = body.get("workspace_id") if isinstance(body, dict) else None
    if query_workspace and body_workspace and query_workspace != body_workspace:
        raise HQError("Workspace mismatch")
    return resolve_workspace(body_workspace or query_workspace, prefs, demo)


def _event(workspace_id: str, kind: str, message: str, ref_id: str | None = None) -> None:
    runtime.add_event({
        "workspace_id": workspace_id,
        "kind": kind,
        "message": message[:500],
        "provenance": "live",
        "created_at": core.timestamp(),
        "ref_id": ref_id,
    })


def _git(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=core.ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def reconciliation() -> dict[str, Any]:
    code, status = _git(["status", "--porcelain"])
    _, diff_stat = _git(["diff", "--stat"])
    _, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    changed = [line for line in status.splitlines() if line.strip()]
    return {
        "available": code != 127,
        "branch": branch or None,
        "dirty": bool(changed),
        "changed": changed[:50],
        "diff_stat": diff_stat,
        "pushed": False,
        "note": "Read-only. The headquarters shell does not commit or push.",
    }


def _store_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs), True
    except sqlite3.Error:
        return None, False


def _project_reviews(workspace_id: str) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    folders = []
    if workspace_id == "local":
        if core.PROJECTS.exists():
            folders = [p for p in sorted(core.PROJECTS.iterdir()) if p.is_dir() and not p.is_symlink()]
    else:
        try:
            folders = [core.project_path(workspace_id)]
        except ValueError:
            return []
    for folder in folders:
        review_dir = folder / "reviews"
        if not review_dir.is_dir():
            continue
        for path in sorted(review_dir.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                review = core.read_json(path)
                project = core.read_json(folder / "project.json")
            except (OSError, json.JSONDecodeError, KeyError):
                continue
            status = review.get("status")
            mapped = {"pending": "pending", "accepted": "approved", "changes_requested": "denied"}.get(status, status)
            reviews.append({
                "id": review.get("id"),
                "workspace_id": project.get("id", folder.name),
                "source": "project-review",
                "subject_type": "project-review",
                "subject_id": review.get("id"),
                "title": review.get("title", "Review"),
                "detail": f"Project {project.get('name', folder.name)}. Evidence {review.get('evidence', '')}. Ref {review.get('ref', '')}. Accepting a demo does not merge or deploy.",
                "status": mapped,
                "source_status": status,
                "requested_at": review.get("requested"),
                "decided_at": review.get("decided"),
                "project": project.get("id", folder.name),
            })
    return reviews


def _project_events(workspace_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if workspace_id == "local":
        for event in core.state().get("events", []):
            events.append({
                "id": f"project-{event.get('time', '')}-{len(events)}",
                "workspace_id": "local",
                "kind": event.get("event", "project"),
                "message": f"{event.get('project', 'Project')}: {event.get('message', '')}",
                "provenance": "live",
                "created_at": event.get("time"),
                "ref_id": None,
            })
        return events
    try:
        folder = core.project_path(workspace_id)
    except ValueError:
        return []
    project = core.read_json(folder / "project.json")
    for event in core.recent_events(folder):
        events.append({
            "id": f"project-{event.get('time', '')}-{len(events)}",
            "workspace_id": workspace_id,
            "kind": event.get("event", "project"),
            "message": f"{project.get('name', workspace_id)}: {event.get('message', '')}",
            "provenance": "live",
            "created_at": event.get("time"),
            "ref_id": None,
        })
    return events


def _canonical_agents() -> list[dict[str, Any]]:
    agents = []
    if not AGENT_DIR.is_dir():
        return agents
    for path in sorted(AGENT_DIR.glob("*.json")):
        if path.is_symlink():
            continue
        record = _load_json(path)
        record["origin"] = "canonical"
        record["definition_status"] = record.get("status", "defined")
        agents.append(record)
    return agents


def _proposed_agents(workspace_id: str) -> list[dict[str, Any]]:
    agents = []
    if not PROPOSALS_DIR.is_dir():
        return agents
    for path in sorted(PROPOSALS_DIR.glob("*.json")):
        if path.is_symlink() or path.name.startswith("."):
            continue
        try:
            record = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        owner = record.get("workspace_id")
        if workspace_id != "local" and owner not in {workspace_id, None}:
            continue
        record["origin"] = "proposed"
        record["definition_status"] = "proposed"
        agents.append(record)
    return agents


def _overlay_agent(agent: dict[str, Any], probes: dict[str, dict], runs: list[dict], status_run: dict | None, now: str) -> dict[str, Any]:
    adapter = probes.get(agent.get("adapter", ""))
    running = next((item for item in runs if item.get("agent_id") == agent["id"] and item.get("status") == "running"), None)
    agent_error = None
    for item in runs:
        if item.get("agent_id") == agent["id"] and item.get("error"):
            agent_error = item
            break
    if agent.get("origin") == "proposed":
        runtime_status = "proposed"
    elif adapter is None or adapter.get("status") == "offline":
        runtime_status = "offline"
    elif running:
        runtime_status = "running"
    else:
        runtime_status = "available"
    last_error = None
    if agent_error:
        last_error = {
            "message": agent_error.get("error"),
            "at": agent_error.get("finished_at") or agent_error.get("started_at"),
            "run_id": agent_error.get("id"),
        }
    elif runtime_status == "offline":
        label = adapter["label"] if adapter else agent.get("adapter", "Adapter")
        at = adapter.get("checked_at") if adapter else now
        run_id = None
        if status_run and status_run.get("error") and adapter and adapter["label"] in str(status_run.get("error")):
            at = status_run.get("finished_at") or at
            run_id = status_run.get("id")
        last_error = {
            "message": f"{label} is offline. No heartbeat was recorded.",
            "at": at,
            "run_id": run_id,
        }
    return {
        **agent,
        "runtime_status": runtime_status,
        "checked_at": (adapter or {}).get("checked_at", now),
        "adapter_status": (adapter or {}).get("status", "offline"),
        "running_since": running.get("started_at") if running else None,
        "last_error": last_error,
        "provenance": "live",
    }


def _live_collections(workspace_id: str) -> dict[str, Any]:
    now = core.timestamp()
    probes = providers.probe(now)
    probe_map = {item["id"]: item for item in probes}
    tasks, tasks_ok = _store_call(runtime.list_tasks, workspace_id)
    schedules, schedules_ok = _store_call(runtime.list_schedules, workspace_id)
    runs, runs_ok = _store_call(runtime.list_runs, workspace_id)
    approvals, approvals_ok = _store_call(runtime.list_approvals, workspace_id)
    events, events_ok = _store_call(runtime.list_events, workspace_id)
    running, _running_ok = _store_call(runtime.running_runs, workspace_id)
    status_run, _status_ok = _store_call(runtime.latest_status_run, workspace_id)
    try:
        runtime.ping()
        database = "ok"
    except sqlite3.Error:
        database = "unavailable"
    agents = []
    for agent in _canonical_agents() + _proposed_agents(workspace_id):
        agents.append(_overlay_agent(agent, probe_map, runs or [], status_run if runs_ok else None, now))
    workspaces = [{
        "id": "local",
        "name": "This machine",
        "kind": "machine",
        "summary": "Local operational records for the headquarters shell.",
        "created_at": None,
        "provenance": "live",
    }]
    for project in core.state().get("projects", []):
        workspaces.append({
            "id": project["id"],
            "name": project["name"],
            "kind": "repository",
            "summary": project.get("summary", ""),
            "created_at": project.get("created"),
            "stage": project.get("stage"),
            "provenance": "live",
        })
    applications = []
    for app in _load_json(SYSTEM_DIR / "applications.json"):
        applications.append({**app, "provenance": "live", "status": "ready"})
    knowledge = []
    for name in core.state().get("skills", []):
        knowledge.append({
            "id": name,
            "name": name,
            "summary": "Versioned workflow instruction.",
            "kind": "skill",
            "provenance": "live",
            "status": "versioned",
        })
    docs = core.ROOT / "docs"
    if docs.is_dir():
        for path in sorted(docs.glob("*.md")):
            knowledge.append({
                "id": path.stem.lower(),
                "name": path.name,
                "summary": "Repository document.",
                "kind": "document",
                "provenance": "live",
                "status": "versioned",
            })
    merged_events = []
    if events_ok and events is not None:
        for item in events:
            item = dict(item)
            item["provenance"] = "live"
            merged_events.append(item)
    merged_events.extend(_project_events(workspace_id))
    merged_events.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    review_rows = _project_reviews(workspace_id)
    approval_rows = None if not approvals_ok else [dict(item, source="runtime", provenance="live") for item in approvals or []]
    if approval_rows is not None:
        approval_rows.extend(review_rows)
    last_error = None
    if runs_ok:
        errored, ok = _store_call(runtime.latest_error, workspace_id)
        if ok and errored:
            last_error = {
                "message": errored.get("error"),
                "at": errored.get("finished_at") or errored.get("started_at"),
                "run_id": errored.get("id"),
            }
    return {
        "banner": None,
        "workspaces": workspaces,
        "applications": (applications, "live"),
        "agents": (agents, "live"),
        "tasks": (tasks, "live" if tasks_ok else "unavailable"),
        "schedules": (schedules, "live" if schedules_ok else "unavailable"),
        "runs": (runs, "live" if runs_ok else "unavailable"),
        "approvals": (approval_rows, "live" if approvals_ok else "unavailable"),
        "events": (merged_events if events_ok else None, "live" if events_ok else "unavailable"),
        "knowledge": (knowledge, "live"),
        "integrations": (probes, "live"),
        "running": running or [],
        "database": database,
        "last_error": last_error,
        "generated_probe_at": now,
    }


def _demo_collections() -> dict[str, Any]:
    raw = _fixtures()
    def tagged(rows: list[dict]) -> tuple[list[dict], str]:
        tagged_rows = []
        for row in rows:
            item = dict(row)
            item["provenance"] = "demo"
            tagged_rows.append(item)
        return tagged_rows, "demo"
    return {
        "banner": raw["banner"],
        "workspaces": [dict(item, provenance="demo") for item in raw["workspaces"]],
        "applications": tagged(raw["applications"]),
        "agents": tagged(raw["agents"]),
        "tasks": tagged(raw["tasks"]),
        "schedules": tagged(raw["schedules"]),
        "runs": tagged(raw["runs"]),
        "approvals": tagged(raw["approvals"]),
        "events": tagged(raw["events"]),
        "knowledge": tagged(raw["knowledge"]),
        "integrations": tagged(raw["integrations"]),
        "running": [],
        "database": "fixture",
        "last_error": raw["agents"][1]["last_error"],
        "generated_probe_at": "2020-01-01T00:00:00+00:00",
    }


def _bundle(query_workspace: str | None) -> tuple[str, dict[str, Any], dict[str, Any], bool]:
    prefs = _prefs()
    demo = bool(prefs.get("demo"))
    workspace_id = resolve_workspace(query_workspace, prefs, demo)
    collections = _demo_collections() if demo else _live_collections(workspace_id)
    return workspace_id, prefs, collections, demo


def _history_for(schedule_id: str, runs: list[dict] | None) -> list[dict[str, Any]]:
    if not runs:
        return []
    history = []
    for run in runs:
        if run.get("schedule_id") != schedule_id:
            continue
        history.append({
            "id": run.get("id"),
            "status": run.get("status"),
            "outcome": run.get("outcome"),
            "error": run.get("error"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
        })
    return history[:8]


def _card_from_fields(item_id: str, kind: str, name: str, status: str, summary: str, provenance: str, fields: list, actions: list | None = None, history: list | None = None) -> dict[str, Any]:
    card = {
        "id": item_id,
        "type": kind,
        "name": name,
        "status": status or "unknown",
        "summary": summary or "",
        "provenance": provenance,
        "fields": [[label, "" if value is None else str(value)] for label, value in fields],
        "actions": [] if provenance == "demo" else (actions or []),
    }
    if history is not None:
        card["history"] = history
    return card


def _cards(resource: str, rows: list[dict] | None, provenance: str, runs: list[dict] | None = None) -> list[dict[str, Any]]:
    if rows is None:
        return []
    cards = []
    for row in rows:
        item_provenance = row.get("provenance", provenance)
        if resource == "workspace":
            cards.append(_card_from_fields(row["id"], "workspace", row["name"], row.get("stage") or row.get("kind", "ready"), row.get("summary", ""), item_provenance, [
                ["Kind", row.get("kind")],
                ["Created", row.get("created_at")],
            ]))
        elif resource == "application":
            cards.append(_card_from_fields(row["id"], "application", row["name"], row.get("status", "ready"), row.get("summary", ""), item_provenance, [
                ["Kind", row.get("kind")],
            ]))
        elif resource == "agent":
            last_error = row.get("last_error") or {}
            actions = [] if row.get("origin") == "proposed" else []
            cards.append(_card_from_fields(row["id"], "agent", row["name"], row.get("runtime_status", "unknown"), row.get("purpose", ""), item_provenance, [
                ["Status", row.get("runtime_status")],
                ["Adapter", row.get("adapter")],
                ["Adapter availability", row.get("adapter_status")],
                ["Checked", row.get("checked_at")],
                ["Running since", row.get("running_since") or "Not running"],
                ["Last error", last_error.get("message") or "None"],
                ["Last error at", last_error.get("at") or "None"],
                ["Origin", row.get("origin")],
                ["Installed", "no" if row.get("origin") == "proposed" else "definition"],
            ], actions))
        elif resource == "task":
            cards.append(_card_from_fields(row["id"], "task", row["title"], row.get("status", "open"), row.get("detail", ""), item_provenance, [
                ["Status", row.get("status")],
                ["Created", row.get("created_at")],
                ["Updated", row.get("updated_at")],
            ]))
        elif resource == "schedule":
            enabled = bool(row.get("enabled"))
            if row.get("kind") == "external-action" and not enabled:
                status = "awaiting-approval"
            else:
                status = "enabled" if enabled else "paused"
            actions = ["run"]
            if row.get("kind") == "external-action" and status == "awaiting-approval":
                actions.extend(["approve", "deny"])
            elif enabled:
                actions.append("pause")
            else:
                actions.append("resume")
            cards.append(_card_from_fields(row["id"], "schedule", row["name"], status, row.get("kind", ""), item_provenance, [
                ["Kind", row.get("kind")],
                ["Enabled", "yes" if enabled else "no"],
                ["Cadence seconds", row.get("cadence_seconds") if row.get("cadence_seconds") is not None else "manual"],
                ["Last run", row.get("last_run_at") or "None"],
                ["Next run", row.get("next_run_at") or "Manual"],
                ["Approval", row.get("approval_id") or "None"],
            ], actions, _history_for(row["id"], runs)))
        elif resource == "knowledge":
            cards.append(_card_from_fields(row["id"], "knowledge", row["name"], row.get("status", "versioned"), row.get("summary", ""), item_provenance, [
                ["Kind", row.get("kind")],
            ]))
        elif resource == "run":
            cards.append(_card_from_fields(row["id"], "run", row.get("kind", "run"), row.get("status", "unknown"), row.get("outcome", ""), item_provenance, [
                ["Status", row.get("status")],
                ["Started", row.get("started_at")],
                ["Finished", row.get("finished_at") or "In progress"],
                ["Error", row.get("error") or "None"],
                ["Schedule", row.get("schedule_id") or "None"],
                ["Agent", row.get("agent_id") or "None"],
            ]))
        elif resource == "approval":
            actions = ["approve", "deny"] if row.get("status") == "pending" else []
            if row.get("source") == "project-review" and row.get("status") == "pending":
                actions = ["accept-demo", "request-changes"]
            cards.append(_card_from_fields(row["id"], "approval", row.get("title", "Approval"), row.get("status", "pending"), row.get("detail", ""), item_provenance, [
                ["Status", row.get("status")],
                ["Source", row.get("source")],
                ["Requested", row.get("requested_at")],
                ["Decided", row.get("decided_at") or "Not decided"],
            ], actions))
        elif resource == "integration":
            cards.append(_card_from_fields(row["id"], "integration", row.get("label", row["id"]), row.get("status", "offline"), row.get("detail", ""), item_provenance, [
                ["Status", row.get("status")],
                ["Network", row.get("network")],
                ["Locality", row.get("locality")],
                ["Probe", row.get("probe")],
                ["Checked", row.get("checked_at")],
            ]))
    return cards


def _count_status(cards: list[dict], status: str) -> int:
    return sum(1 for card in cards if card.get("status") == status)


def build_domains(collections: dict[str, Any]) -> list[dict[str, Any]]:
    runs = collections["runs"][0] or []
    resource_rows = {
        "workspace": (collections["workspaces"], "live" if collections["banner"] is None else "demo"),
        "application": collections["applications"],
        "agent": collections["agents"],
        "task": collections["tasks"],
        "schedule": collections["schedules"],
        "knowledge": collections["knowledge"],
        "run": collections["runs"],
        "approval": collections["approvals"],
        "integration": collections["integrations"],
    }
    domains = []
    for spec in _domains():
        rows, provenance = resource_rows[spec["resource"]]
        if spec["resource"] == "workspace":
            provenance = "demo" if collections["banner"] else "live"
        cards = _cards(spec["resource"], rows, provenance, runs if spec["resource"] == "schedule" else None)
        if provenance == "unavailable":
            kpis = [["State", "unavailable"]]
            summary = "This module is unavailable. No count is shown."
        else:
            summary = spec["summary"]
            if spec["resource"] == "agent":
                kpis = [["Defined", str(len(cards))], ["Offline", str(_count_status(cards, "offline"))], ["Running", str(_count_status(cards, "running"))]]
            elif spec["resource"] == "integration":
                kpis = [["Adapters", str(len(cards))], ["Offline", str(_count_status(cards, "offline"))]]
            elif spec["resource"] == "approval":
                kpis = [["Pending", str(_count_status(cards, "pending"))], ["Records", str(len(cards))]]
            else:
                kpis = [["Records", str(len(cards))]]
        domains.append({
            "id": spec["id"],
            "label": spec["label"],
            "summary": summary,
            "color": spec["color"],
            "resource": spec["resource"],
            "provenance": provenance,
            "kpis": kpis,
            "children": cards,
        })
    return domains


def universe(query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        workspace_id, _prefs_row, collections, demo = _bundle(query_workspace)
        payload = {
            "banner": collections["banner"],
            "workspaces": collections["workspaces"],
            "domains": build_domains(collections),
            "events": (collections["events"][0] or [])[:12],
        }
        provenance = "demo" if demo else "live"
        return 200, envelope(payload, workspace_id, provenance, demo)


def system_status(query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        workspace_id, prefs, collections, demo = _bundle(query_workspace)
        git_state = {"available": False, "branch": None, "dirty": False, "pushed": False, "note": "Not probed in demo mode."}
        adapters = collections["integrations"][0] or []
        if not demo:
            git_state = reconciliation()
        data = {
            "service": "online",
            "interface": "headquarters",
            "bind": "127.0.0.1",
            "version": "0.4.0-hq",
            "database": collections["database"],
            "demo": demo,
            "locality_mode": prefs.get("locality_mode"),
            "adapters": adapters,
            "git": git_state,
            "last_error": collections["last_error"],
        }
        return 200, envelope(data, workspace_id, "demo" if demo else "live", demo)


def list_named(name: str, query_workspace: str | None, since: str | None = None) -> tuple[int, dict[str, Any]]:
    with _ops:
        workspace_id, _prefs_row, collections, demo = _bundle(query_workspace)
        mapping = {
            "workspaces": (collections["workspaces"], "demo" if demo else "live"),
            "apps": collections["applications"],
            "agents": collections["agents"],
            "tasks": collections["tasks"],
            "schedules": collections["schedules"],
            "runs": collections["runs"],
            "approvals": collections["approvals"],
            "events": collections["events"],
        }
        rows, provenance = mapping[name]
        if provenance == "unavailable":
            raise HQError(f"{name} store is unavailable", 503, "unavailable")
        if name == "events" and since:
            # Query strings treat "+" as a space, so accept that form too.
            since = since.replace(" ", "+")
            if since.endswith("Z"):
                since = since[:-1] + "+00:00"
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", since):
                raise HQError("since must be a UTC timestamp")
            rows = [row for row in rows if (row.get("created_at") or "") > since]
        return 200, envelope(rows, workspace_id, provenance, demo)


def git_reconciliation(query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        if prefs.get("demo"):
            raise HQError("Repository reconciliation is hidden in demo mode so fixtures stay separate from Git.", 409, "demo")
        workspace_id = resolve_workspace(query_workspace, prefs, False)
        return 200, envelope(reconciliation(), workspace_id, "live", False)


def list_themes() -> tuple[int, dict[str, Any]]:
    catalog = _load_json(SYSTEM_DIR / "themes.json")
    return 200, envelope(catalog["themes"], "local", "live", False)


def get_preferences_view() -> tuple[int, dict[str, Any]]:
    prefs = _prefs()
    return 200, envelope(prefs, prefs.get("workspace_id") or "local", "demo" if prefs.get("demo") else "live", bool(prefs.get("demo")))


def update_preferences(changes: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        if "theme" in changes:
            if changes["theme"] not in THEMES:
                raise HQError("Theme must be hud, dark, or light.")
            prefs["theme"] = changes["theme"]
        if "demo" in changes:
            if not isinstance(changes["demo"], bool):
                raise HQError("Demo mode must be true or false.")
            prefs["demo"] = changes["demo"]
        if "voice_out" in changes:
            if not isinstance(changes["voice_out"], bool):
                raise HQError("Voice out must be true or false.")
            prefs["voice_out"] = changes["voice_out"]
        if "locality_mode" in changes:
            if changes["locality_mode"] not in {"local-only", "provider-choice"}:
                raise HQError("Locality mode must be local-only or provider-choice.")
            prefs["locality_mode"] = changes["locality_mode"]
        if "workspace_id" in changes:
            if prefs.get("demo") and changes.get("demo") is not False:
                raise HQError("Workspace changes are paused in demo mode.", 409, "demo")
            # Resolve against the saved demo flag after this request applies it.
            probe_prefs = {**prefs, "demo": False}
            prefs["workspace_id"] = resolve_workspace(str(changes["workspace_id"]), probe_prefs, False)
        if "layout" in changes:
            layout = changes["layout"]
            if not isinstance(layout, dict):
                raise HQError("Layout must be an object.")
            domain = layout.get("domain")
            selected = layout.get("selected")
            known = {item["id"] for item in _domains()}
            if domain is not None and domain not in known:
                raise HQError("Unknown domain.")
            if selected is not None and (not isinstance(selected, str) or len(selected) > 64):
                raise HQError("Invalid selection.")
            prefs["layout"] = {"domain": domain, "selected": selected}
        try:
            runtime.save_preferences(prefs)
        except sqlite3.Error as exc:
            raise HQError("Preference store is unavailable", 503, "unavailable") from exc
        return 200, envelope(prefs, prefs.get("workspace_id") or "local", "demo" if prefs.get("demo") else "live", bool(prefs.get("demo")))


def create_task(body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        title = _text(body.get("title"), 120, "a title")
        detail = _text(body.get("detail") or body.get("description") or "Local task.", 4000, "a detail")
        now = core.timestamp()
        record = {
            "id": _new_id("t-"),
            "workspace_id": workspace_id,
            "title": title,
            "detail": detail,
            "status": "open",
            "created_at": now,
            "updated_at": now,
        }
        runtime.insert_task(record)
        _event(workspace_id, "task_created", f"Created task {title}.", record["id"])
        return 201, envelope(record, workspace_id, "live", False)


def _approval_record(workspace_id: str, subject_id: str, title: str, detail: str) -> dict[str, Any]:
    record = {
        "id": _new_id("a-"),
        "workspace_id": workspace_id,
        "subject_type": "schedule",
        "subject_id": subject_id,
        "title": title,
        "detail": detail,
        "status": "pending",
        "requested_at": core.timestamp(),
        "decided_at": None,
        "decision_note": None,
    }
    runtime.insert_approval(record)
    return record


def create_schedule(body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        name = _text(body.get("name") or body.get("title"), 120, "a name")
        kind = body.get("kind")
        if kind not in KINDS:
            raise HQError("Kind must be adapter-status, local-note, or external-action.")
        cadence = body.get("cadence_seconds", None)
        if cadence is None:
            cadence_seconds = None
        elif isinstance(cadence, int) and not isinstance(cadence, bool) and 60 <= cadence <= 30 * 24 * 3600:
            cadence_seconds = cadence
        else:
            raise HQError("Cadence must be empty for manual, or between 60 and 2592000 seconds.")
        now = core.timestamp()
        task = {
            "id": _new_id("t-"),
            "workspace_id": workspace_id,
            "title": name,
            "detail": f"Scheduled local work ({kind}).",
            "status": "scheduled",
            "created_at": now,
            "updated_at": now,
        }
        runtime.insert_task(task)
        schedule_id = _new_id("s-")
        approval_id = None
        enabled = 1
        if kind == "external-action":
            approval = _approval_record(
                workspace_id,
                schedule_id,
                f"Approve external action: {name}",
                "This records an owner decision only. The headquarters shell does not send email, call a provider, or run a shell command.",
            )
            approval_id = approval["id"]
            enabled = 0
        schedule = {
            "id": schedule_id,
            "workspace_id": workspace_id,
            "task_id": task["id"],
            "name": name,
            "kind": kind,
            "cadence_seconds": cadence_seconds,
            "enabled": enabled,
            "approval_id": approval_id,
            "created_at": now,
            "updated_at": now,
            "next_run_at": _plus(now, cadence_seconds) if cadence_seconds else None,
            "last_run_at": None,
        }
        runtime.insert_schedule(schedule)
        _event(workspace_id, "schedule_created", f"Created schedule {name}.", schedule_id)
        return 201, envelope({"schedule": schedule, "task": task, "approval_id": approval_id}, workspace_id, "live", False)


def update_schedule(schedule_id: str, body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        if not re.fullmatch(r"s-[0-9a-f]{8}", schedule_id):
            raise HQError("Schedule not found", 404)
        current = runtime.get_schedule(schedule_id)
        if not current or current["workspace_id"] != workspace_id:
            raise HQError("Schedule not found", 404)
        if "enabled" not in body or not isinstance(body["enabled"], bool):
            raise HQError("Enabled must be true or false.")
        if body["enabled"] and current["kind"] == "external-action":
            approval = runtime.get_approval(current["approval_id"]) if current.get("approval_id") else None
            if not approval or approval.get("status") != "approved":
                raise HQError("External schedules stay paused until an owner approves them, and approval still does not execute them.", 409)
        updated = runtime.update_schedule_fields(schedule_id, {"enabled": 1 if body["enabled"] else 0, "updated_at": core.timestamp()})
        return 200, envelope(updated, workspace_id, "live", False)


def _finish_run(schedule: dict[str, Any], status: str, outcome: str, error: str | None, started: str, agent_id: str | None = None) -> dict[str, Any]:
    finished = core.timestamp()
    record = {
        "id": _new_id("n-"),
        "workspace_id": schedule["workspace_id"],
        "schedule_id": schedule["id"],
        "agent_id": agent_id,
        "kind": schedule["kind"],
        "status": status,
        "outcome": outcome[:4000],
        "error": error,
        "started_at": started,
        "finished_at": finished,
    }
    runtime.insert_run(record)
    runtime.update_schedule_fields(schedule["id"], {"last_run_at": finished, "updated_at": finished})
    _event(schedule["workspace_id"], "run_finished", f"{schedule['name']} finished with status {status}.", record["id"])
    return record


def _execute(schedule: dict[str, Any]) -> dict[str, Any]:
    started = core.timestamp()
    kind = schedule["kind"]
    if kind == "external-action":
        approval = runtime.get_approval(schedule["approval_id"]) if schedule.get("approval_id") else None
        if not approval or approval.get("status") != "approved":
            return _finish_run(schedule, "blocked", "Nothing was sent.", "Waiting for owner approval. Nothing was sent.", started)
        return _finish_run(
            schedule,
            "not_executed",
            "The owner decision was recorded. No external service was contacted.",
            "The owner approved this external action. The headquarters shell did not execute it.",
            started,
        )
    if kind == "local-note":
        return _finish_run(schedule, "completed", "Recorded a local note. No external service was contacted.", None, started)
    if kind == "adapter-status":
        adapters = providers.probe(started)
        offline = [item["label"] for item in adapters if item["status"] == "offline" and item["locality"] == "local-cli"]
        error = None
        if offline:
            error = "Offline: " + ", ".join(offline) + ". No heartbeat was recorded."
        outcome = json.dumps({"adapters": adapters}, ensure_ascii=False)
        return _finish_run(schedule, "completed", outcome, error, started)
    return _finish_run(schedule, "error", "The schedule kind is not supported.", "Unsupported schedule kind.", started)


def run_schedule(schedule_id: str, query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = resolve_workspace(query_workspace, prefs, False)
        if not re.fullmatch(r"s-[0-9a-f]{8}", schedule_id):
            raise HQError("Schedule not found", 404)
        schedule = runtime.get_schedule(schedule_id)
        if not schedule or schedule["workspace_id"] != workspace_id:
            raise HQError("Schedule not found", 404)
        try:
            record = _execute(schedule)
        except sqlite3.Error as exc:
            raise HQError("Run store is unavailable", 503, "unavailable") from exc
        return 201, envelope(record, workspace_id, "live", False)


def tick(now: str | None = None) -> list[dict[str, Any]]:
    with _ops:
        moment = now or core.timestamp()
        results = []
        try:
            due = runtime.due_schedules(moment)
        except sqlite3.Error:
            return results
        for schedule in due:
            try:
                record = _execute(schedule)
            except sqlite3.Error:
                continue
            if schedule.get("cadence_seconds"):
                runtime.update_schedule_fields(schedule["id"], {
                    "next_run_at": _plus(moment, int(schedule["cadence_seconds"])),
                    "updated_at": core.timestamp(),
                })
            results.append(record)
        return results


def decide_approval(approval_id: str, body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        decision = body.get("decision")
        note = body.get("note")
        if note is not None:
            note = _text(note, 400, "a note")
        if approval_id.startswith("r-"):
            if decision not in {"accepted", "changes_requested"}:
                raise HQError("Project reviews use accepted or changes_requested.")
            project = body.get("project") or (workspace_id if workspace_id != "local" else None)
            if not project:
                for review in _project_reviews("local"):
                    if review["id"] == approval_id:
                        project = review["project"]
                        break
            if not project:
                raise HQError("Review not found", 404)
            try:
                review = core.decide_review(project, approval_id, decision)
            except ValueError as exc:
                message = str(exc)
                status = 404 if "not found" in message.lower() else 400
                raise HQError(message, status) from exc
            _event(project, "owner_decision", f"Recorded {decision} for {review['title']}. No merge or deploy.", approval_id)
            return 200, envelope(review, project, "live", False)
        if not re.fullmatch(r"a-[0-9a-f]{8}", approval_id):
            raise HQError("Approval not found", 404)
        if decision not in {"approved", "denied"}:
            raise HQError("Decision must be approved or denied.")
        current = runtime.get_approval(approval_id)
        if not current or (workspace_id != "local" and current["workspace_id"] != workspace_id):
            raise HQError("Approval not found", 404)
        if current["status"] != "pending":
            raise HQError("This approval has already been decided.")
        updated = runtime.update_approval(approval_id, {
            "status": decision,
            "decided_at": core.timestamp(),
            "decision_note": note,
        })
        if decision == "approved":
            message = "Owner approved an external action. The headquarters shell did not execute it."
        else:
            message = "Owner denied an external action. Nothing was sent."
        _event(current["workspace_id"], "approval_decision", message, approval_id)
        return 200, envelope(updated, current["workspace_id"], "live", False)


def _local_reply(text: str) -> str:
    lowered = text.lower()
    catalog = "Domains: workspaces, applications, agents, tasks, schedules, knowledge, runs, approvals, and integrations."
    if "status" in lowered:
        return "Adapter status is a path lookup, not a heartbeat. " + catalog
    for word in ("workspace", "application", "agent", "task", "schedule", "knowledge", "run", "approval", "integration"):
        if word in lowered:
            return f"Open the {word} domain from the dock. The local headquarters did not call a model. " + catalog
    return "The headquarters shell stored your command locally and did not call a model. " + catalog


def post_message(conversation_id: str, body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        if not re.fullmatch(r"[a-z0-9-]{1,40}", conversation_id):
            raise HQError("Invalid conversation id")
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        text = _text(body.get("text") or body.get("message"), 4000, "a message")
        provider_id = body.get("provider") or "local-hq"
        locality = body.get("locality") or "local"
        if locality not in LOCALITY:
            raise HQError("Locality must be local or provider-choice.")
        spec = providers.get_adapter(str(provider_id))
        if spec is None:
            raise HQError("Unknown provider.")
        mode = prefs.get("locality_mode", "local-only")
        if mode == "local-only" and spec.network != "local":
            raise HQError("Local-only mode does not call cloud or provider-dependent adapters.")
        if locality == "local" and spec.network == "cloud":
            raise HQError("Refusing to contact a cloud provider while locality is local.")
        now = core.timestamp()
        runtime.ensure_conversation({"id": conversation_id, "workspace_id": workspace_id, "created_at": now})
        user_message = runtime.add_message({
            "id": _new_id("m-"),
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "role": "user",
            "body": text,
            "provider": spec.id,
            "locality": locality,
            "status": "stored",
            "created_at": now,
        })
        probed = {item["id"]: item for item in providers.probe(now)}
        adapter_status = probed[spec.id]["status"]
        sent = False
        if spec.id == "local-hq":
            reply = _local_reply(text)
            result_status = "completed"
        elif adapter_status == "offline":
            reply = f"{spec.label} is offline on this machine. No reply was generated and nothing was sent."
            result_status = "offline"
        else:
            reply = f"Saved locally for {spec.label}. The headquarters shell did not start the adapter or send this text."
            result_status = "queued"
        assistant = runtime.add_message({
            "id": _new_id("m-"),
            "conversation_id": conversation_id,
            "workspace_id": workspace_id,
            "role": "assistant",
            "body": reply,
            "provider": spec.id,
            "locality": locality,
            "status": result_status,
            "created_at": core.timestamp(),
        })
        _event(workspace_id, "message_stored", f"Stored a {spec.id} message with status {result_status}.", conversation_id)
        return 201, envelope({
            "conversation_id": conversation_id,
            "sent_externally": sent,
            "user_message": {"id": user_message["id"], "text": text, "created_at": user_message["created_at"]},
            "result": {
                "status": result_status,
                "provider": spec.id,
                "locality": locality,
                "text": assistant["body"],
                "created_at": assistant["created_at"],
            },
        }, workspace_id, "live", False)


def create_agent_proposal(body: dict[str, Any], query_workspace: str | None) -> tuple[int, dict[str, Any]]:
    with _ops:
        prefs = _prefs()
        _reject_demo(prefs)
        workspace_id = _workspace_arg(query_workspace, body, prefs, False)
        name = _text(body.get("name"), 80, "a name")
        purpose = _text(body.get("purpose"), 4000, "a purpose")
        try:
            slug = core.slugify(name)
        except ValueError as exc:
            raise HQError("Use a shorter name with letters or numbers.") from exc
        adapter_id = body.get("adapter") or "local-hq"
        locality = body.get("locality") or "local"
        spec = providers.get_adapter(str(adapter_id))
        if spec is None:
            raise HQError("Unknown adapter.")
        if locality not in LOCALITY:
            raise HQError("Locality must be local or provider-choice.")
        if locality == "local" and spec.network == "cloud":
            raise HQError("A cloud adapter cannot be saved as local-only.")
        if (AGENT_DIR / f"{slug}.json").is_file() or (PROPOSALS_DIR / f"{slug}.json").is_file():
            raise HQError("An agent with that name already exists.")
        record = {
            "id": slug,
            "name": name,
            "purpose": purpose,
            "adapter": spec.id,
            "locality": locality,
            "network": spec.network,
            "workspace_id": workspace_id,
            "status": "proposed",
            "installed": False,
            "created_at": core.timestamp(),
            "schema": "infinite-arch.agent/1",
        }
        PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
        json_path = PROPOSALS_DIR / f"{slug}.json"
        md_path = PROPOSALS_DIR / f"{slug}.md"
        core.atomic_json(json_path, record)
        md_path.write_text(
            f"# {name}\n\n"
            "Status: proposed. Not installed. Not granted permissions. Not pushed.\n\n"
            f"## Purpose\n\n{purpose}\n\n"
            f"## Adapter\n\n{spec.id} ({spec.network})\n\n"
            "This file is a proposed Git change. Review it and commit it yourself if you want it in the repository. "
            "The headquarters shell does not push it.\n",
            encoding="utf-8",
        )
        _event(workspace_id, "agent_proposed", f"Proposed agent definition {name}. It is not installed.", slug)

        def display_path(path) -> str:
            root = str(core.ROOT)
            prefix = root if root.endswith("/") else root + "/"
            text = str(path)
            if text.startswith(prefix):
                return text[len(prefix):]
            return "proposals/agents/" + path.name

        return 201, envelope({
            "agent": record,
            "json_path": display_path(json_path),
            "markdown_path": display_path(md_path),
            "pushed": False,
        }, workspace_id, "live", False)
