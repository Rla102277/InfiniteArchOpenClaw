"""Read-only typed views for the WHOPR command view, GET only.

Each `/api/v1/<resource>` is a thin projection over the runtime snapshot: same loopback and
origin checks as every other GET route, and never anything the snapshot does not already
expose. No payloads, prompts, prompt hashes or secrets are projected. Writes stay on the
existing action API.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(value, limit=300):
    text = str(value or "")
    return text[:limit]


def _fields(row: dict, names: tuple[str, ...]) -> dict:
    return {name: row.get(name) for name in names if name in row}


def _task(row: dict) -> dict:
    # The payload holds prompts and arguments; it is never projected.
    out = _fields(row, ("id", "title", "operation", "status", "error", "created", "updated"))
    out["result"] = _clip(row.get("result"))
    return out


def _agent(row: dict) -> dict:
    return _fields(row, ("id", "name", "label", "role", "adapter", "availability", "status",
                         "state", "origin", "app", "detail", "last_error", "last_run"))


def _schedule(row: dict) -> dict:
    out = _fields(row, ("id", "name", "workflow", "interval_seconds", "enabled", "next_run"))
    out["history"] = [_fields(h, ("status", "at", "detail")) for h in row.get("history") or []][:5]
    return out


def _approval(row: dict) -> dict:
    # prompt_hash and the bound prompt stay server-side.
    return _fields(row, ("id", "task_id", "title", "status", "adapter", "decided_at", "created"))


def _event(row: dict) -> dict:
    return _fields(row, ("time", "event", "message"))


def _app(entry) -> dict:
    if isinstance(entry, dict):
        return _fields(entry, ("id", "name", "label", "kind", "status", "description",
                               "icon", "version", "counts", "error"))
    return {"name": str(entry)}


def _chat(row: dict) -> dict:
    return _fields(row, ("id", "title", "agent", "app", "created", "updated", "messages"))


def _adapter(row: dict) -> dict:
    return _fields(row, ("id", "label", "available", "status", "detail", "locality", "network"))


def _module_view(entry) -> dict:
    if isinstance(entry, dict):
        views = entry.get("views") or entry.get("cards")
        return _fields(entry, ("id", "name", "status", "error", "counts")) | (
            {"views": views} if views else {})
    return {"name": str(entry)}


def build(resource: str, snapshot: dict) -> dict:
    """One typed resource from a runtime snapshot. Raises ValueError for unknown resources."""
    if resource == "status":
        brain = snapshot.get("brain") or {}
        return {"version": snapshot.get("version"), "storage": snapshot.get("storage"),
                "brain": _fields(brain, ("branch", "head", "reconciled_head", "drift")),
                "adapters": [_adapter(a) for a in snapshot.get("adapters") or []],
                "capabilities": snapshot.get("capabilities") or [],
                "telegram": _fields(snapshot.get("telegram") or {}, ("running", "reachable", "detail"))}
    if resource == "apps":
        modules = snapshot.get("modules")
        apps = [_module_view(v) for v in modules.values()] if isinstance(modules, dict) else []
        return {"apps": [_app(a) for a in snapshot.get("apps") or []] + apps}
    if resource == "agents":
        return {"agents": [_agent(a) for a in snapshot.get("agents") or []]}
    if resource == "tasks":
        return {"tasks": [_task(t) for t in snapshot.get("tasks") or []]}
    if resource == "schedules":
        return {"schedules": [_schedule(s) for s in snapshot.get("schedules") or []]}
    if resource == "runs":
        done = [t for t in snapshot.get("tasks") or [] if t.get("status") in {"succeeded", "failed"}]
        return {"runs": [_task(t) for t in done]}
    if resource == "events":
        return {"events": [_event(e) for e in snapshot.get("events") or []],
                "activity": [_event(e) for e in snapshot.get("activity") or [] if isinstance(e, dict)]}
    if resource == "approvals":
        return {"approvals": [_approval(a) for a in snapshot.get("approvals") or []]}
    if resource == "conversations":
        return {"conversations": [_chat(c) for c in snapshot.get("chats") or []]}
    if resource == "themes":
        return {"themes": snapshot.get("themes") or [],
                "default": (snapshot.get("preferences") or {}).get("theme")}
    raise ValueError("Unknown resource.")


def envelope(resource: str, snapshot: dict) -> dict:
    return {"ok": True, "workspace_id": "local", "provenance": "live", "demo": False,
            "generated_at": _iso(), "data": build(resource, snapshot)}
