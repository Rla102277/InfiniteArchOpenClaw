"""HTTP routing for the local headquarters API."""
from __future__ import annotations

import os
import re

from . import hq

SCHEDULE_RUN = re.compile(r"^/api/v1/schedules/(s-[0-9a-f]{8})/run$")
SCHEDULE_ID = re.compile(r"^/api/v1/schedules/(s-[0-9a-f]{8})$")
APPROVAL = re.compile(r"^/api/v1/approvals/([A-Za-z0-9-]{1,40})/decision$")
MESSAGE = re.compile(r"^/api/v1/conversations/([a-z0-9-]{1,40})/messages$")


def command_center_enabled() -> bool:
    return os.environ.get("HQ_COMMAND_CENTER", "1") != "0"


def _q(query: dict, name: str) -> str | None:
    values = query.get(name) or []
    if not values:
        return None
    value = values[0]
    return value if isinstance(value, str) and value else None


def dispatch(method: str, path: str, query: dict, body: dict | None) -> tuple[int, dict]:
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return hq.fail("Expected an object", 400)
    try:
        return _route(method, path, query, body)
    except hq.HQError as exc:
        return hq.fail(str(exc), exc.status, exc.provenance)


def _route(method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    workspace = _q(query, "workspace")
    since = _q(query, "since")
    if method == "GET" and path == "/api/v1/system/status":
        return hq.system_status(workspace)
    if method == "GET" and path == "/api/v1/reconciliation":
        return hq.git_reconciliation(workspace)
    if method == "GET" and path == "/api/v1/universe":
        return hq.universe(workspace)
    if method == "GET" and path == "/api/v1/workspaces":
        return hq.list_named("workspaces", workspace)
    if method == "GET" and path == "/api/v1/apps":
        return hq.list_named("apps", workspace)
    if method == "GET" and path == "/api/v1/agents":
        return hq.list_named("agents", workspace)
    if method == "POST" and path == "/api/v1/agents":
        return hq.create_agent_proposal(body, workspace)
    if method == "GET" and path == "/api/v1/tasks":
        return hq.list_named("tasks", workspace)
    if method == "POST" and path == "/api/v1/tasks":
        return hq.create_task(body, workspace)
    if method == "GET" and path == "/api/v1/schedules":
        return hq.list_named("schedules", workspace)
    if method == "POST" and path == "/api/v1/schedules":
        return hq.create_schedule(body, workspace)
    matched = SCHEDULE_RUN.match(path)
    if method == "POST" and matched:
        return hq.run_schedule(matched.group(1), workspace)
    matched = SCHEDULE_ID.match(path)
    if method == "PATCH" and matched:
        return hq.update_schedule(matched.group(1), body, workspace)
    if method == "GET" and path == "/api/v1/runs":
        return hq.list_named("runs", workspace)
    if method == "GET" and path == "/api/v1/events":
        return hq.list_named("events", workspace, since)
    if method == "GET" and path == "/api/v1/approvals":
        return hq.list_named("approvals", workspace)
    matched = APPROVAL.match(path)
    if method == "POST" and matched:
        return hq.decide_approval(matched.group(1), body, workspace)
    matched = MESSAGE.match(path)
    if method == "POST" and matched:
        return hq.post_message(matched.group(1), body, workspace)
    if method == "GET" and path == "/api/v1/themes":
        return hq.list_themes()
    if method == "GET" and path == "/api/v1/preferences":
        return hq.get_preferences_view()
    if method == "PUT" and path in {"/api/v1/preferences", "/api/v1/preferences/theme"}:
        if path.endswith("/theme"):
            body = {"theme": body.get("theme")}
        return hq.update_preferences(body)
    return hq.fail("Not found", 404)
