"""Module apps: full applications that mount into the OS (system/contracts/MODULES.md).

The host imports `apps/<id>/<entry>.py` with the app folder on sys.path, calls `create(host)`,
and talks to the returned App only through the methods the contract names. Every call is
wrapped: a broken app reports `{"error": ...}` and never takes the dashboard down. Apps are
cached per brain fingerprint, so editing a manifest reloads them on the next reconcile.
"""
from __future__ import annotations

import importlib
import os
import tempfile
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .storage import private_home

ENTRY = re.compile(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*")
PERMISSIONS = {"hq.write", "model.call", "darkroom.read", "darkroom.write"}
MIME = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png",
        ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8",
        ".webmanifest": "application/manifest+json"}
_lock = threading.Lock()
_cache: dict[str, tuple[str, dict]] = {}


@dataclass
class Host:
    os_root: Path
    domain: Path
    app_dir: Path
    app_id: str
    runtime: object = field(default=None, repr=False)
    brain: object = field(default=None, repr=False)

    def app(self, app_id: str):
        """Another loaded module app, or None. Lets apps call each other without importing
        each other's code."""
        if self.brain is None or app_id == self.app_id:
            return None
        found = load(self.brain, self.runtime).get(app_id)
        return None if found is None or isinstance(found, dict) else found

    @property
    def private_dir(self) -> Path:
        """Where the app keeps throwaway state (caches, SQLite). Outside the checkout, like the OS's own."""
        runtime = self.runtime
        if runtime is not None and getattr(runtime, "home", None):
            base = Path(runtime.home)
        else:
            configured = os.environ.get("AIHQ_HOME", str(Path.home() / "PersonalAIHQ"))
            try:
                base = private_home(configured, self.os_root)
            except ValueError:
                base = Path(tempfile.gettempdir()) / "aihq-apps"
        path = base / "apps" / self.app_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log(self, event: str, message: str) -> None:
        runtime = self.runtime
        if runtime is None:
            return
        with runtime.connect() as connection:
            runtime.event(connection, str(event)[:80], f"[{self.app_id}] " + str(message)[:2000])

    def enqueue(self, title: str, routine: str) -> dict:
        if self.runtime is None:
            raise ValueError("No runtime attached to this host.")
        return self.runtime.action({"action": "task.create", "title": title, "operation": "module.routine",
                                    "payload": {"app": self.app_id, "routine": routine}})


def manifest_ok(item: dict, app_dir: Path) -> None:
    """Validate a kind=module manifest. Raises ValueError with the reason."""
    required = {"schema_version", "id", "name", "description", "kind", "entry", "icon", "views", "web", "permissions", "version"}
    if set(item) != required:
        raise ValueError(f"Module manifest keys must be exactly: {', '.join(sorted(required))}")
    if item["schema_version"] != 1 or not isinstance(item["version"], str) or not item["version"]:
        raise ValueError("Module manifest needs schema_version 1 and a version string.")
    if not isinstance(item["icon"], str) or not 1 <= len(item["icon"]) <= 4:
        raise ValueError("Module icon must be one to four characters.")
    entry = item["entry"]
    if not isinstance(entry, str) or not ENTRY.fullmatch(entry):
        raise ValueError("Module entry must be a dotted module name.")
    entry_file = app_dir.joinpath(*entry.split(".")).with_suffix(".py")
    if not entry_file.resolve().is_relative_to(app_dir.resolve()) or not entry_file.is_file():
        raise ValueError(f"Module entry not found under apps/{item['id']}/: {entry}")
    views = item["views"]
    if not isinstance(views, list) or not views:
        raise ValueError("Module views must be a nonempty list.")
    seen = set()
    for view in views:
        if not isinstance(view, dict) or not {"id", "label"} <= set(view) <= {"id", "label", "count"}:
            raise ValueError("Each view has id, label and optionally count.")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", str(view["id"])) or view["id"] in seen:
            raise ValueError("View ids must be unique lowercase identifiers.")
        seen.add(view["id"])
    web = item["web"]
    if not isinstance(web, dict) or "script" not in web or not set(web) <= {"script", "style"}:
        raise ValueError("Module web needs a script and optionally a style.")
    for key, value in web.items():
        if not isinstance(value, str) or not value.startswith("web/") or ".." in value.split("/") or value.startswith("/"):
            raise ValueError(f"Module web.{key} must be a relative path under web/.")
    perms = item["permissions"]
    if not isinstance(perms, list) or not set(perms) <= PERMISSIONS or len(perms) != len(set(perms)):
        raise ValueError("Module permissions must be from: " + ", ".join(sorted(PERMISSIONS)))


def safe_call(app, method: str, *args, default=None):
    """Call an optional App method; exceptions and missing methods become values, never raises."""
    function = getattr(app, method, None)
    if function is None:
        return default
    try:
        return function(*args)
    except Exception as exc:  # one broken app never takes the dashboard down
        return {"error": f"{method}: {str(exc)[:400]}"}


def _import(brain, manifest: dict, runtime):
    app_dir = brain.safe("apps/" + manifest["id"])
    host = Host(os_root=brain.root, domain=brain.root.parent, app_dir=app_dir, app_id=manifest["id"], runtime=runtime, brain=brain)
    folder = str(app_dir)
    with _lock:
        top = manifest["entry"].split(".")[0]
        for name in [m for m in list(sys.modules) if m == top or m.startswith(top + ".")]:
            module = sys.modules.get(name)
            file = getattr(module, "__file__", None) or next(iter(getattr(module, "__path__", []) or []), None)
            if not file or not Path(file).resolve().is_relative_to(app_dir.resolve()):
                del sys.modules[name]  # another checkout's copy (or a stale one); load this app's own
        sys.path.insert(0, folder)
        importlib.invalidate_caches()  # the app folder may be newer than the interpreter's path cache
        # apps/ is brain: compiled caches must never land in it. Apps import lazily, so this stays
        # on for the life of the process (the OS is small, stdlib-only Python; the cost is nil).
        sys.dont_write_bytecode = True
        try:
            module = importlib.import_module(manifest["entry"])
            module = importlib.reload(module)
        finally:
            if folder in sys.path:
                sys.path.remove(folder)
    create = getattr(module, "create", None)
    if create is None:
        raise ValueError("Module entry has no create(host).")
    app = create(host)
    if getattr(app, "id", None) != manifest["id"]:
        raise ValueError("App.id must equal the manifest id.")
    return app


def load(brain, runtime=None) -> dict:
    """{id: App or {"error": str}} for every kind=module app in the catalog, plus the agent apps
    defined under hq/os/apps (data, not code; see agentapps.py). Agent apps are re-read whenever
    one of their files changes, so a newly created one shows up on the next call."""
    return {**_load_modules(brain, runtime), **_agent_apps(brain, runtime)}


def _agent_apps(brain, runtime=None) -> dict:
    try:
        from . import agentapps
        return agentapps.adapters(brain.root.parent, runtime)
    except Exception:  # the brain stays up even when an app folder is malformed
        return {}


def _load_modules(brain, runtime=None) -> dict:
    catalog = brain.catalog()
    fingerprint = brain.fingerprint()
    key = str(brain.root)
    with _lock:
        cached = _cache.get(key)
    if cached and cached[0] == fingerprint:
        return cached[1]
    apps = {}
    for manifest in catalog["apps"]:
        if manifest.get("kind") != "module":
            continue
        try:
            apps[manifest["id"]] = _import(brain, manifest, runtime)
        except Exception as exc:
            apps[manifest["id"]] = {"error": str(exc)[:600]}
    with _lock:
        _cache[key] = (fingerprint, apps)
    return apps


def invalidate() -> None:
    with _lock:
        _cache.clear()


def manifests(catalog: dict) -> dict:
    return {item["id"]: item for item in catalog["apps"] if item.get("kind") == "module"}


def snapshot(brain, runtime=None) -> dict:
    """The `modules` key of the OS snapshot: module apps and agent apps alike."""
    out = {}
    loaded = load(brain, runtime)
    all_manifests = dict(manifests(brain.catalog()))
    for app_id, app in loaded.items():
        if app_id not in all_manifests and hasattr(app, "manifest"):
            try:
                all_manifests[app_id] = app.manifest()
            except Exception as exc:
                all_manifests[app_id] = {"id": app_id, "name": app_id, "kind": "agent", "error": str(exc)[:200]}
    for app_id, manifest in all_manifests.items():
        app = loaded.get(app_id)
        if isinstance(app, dict):
            out[app_id] = {"manifest": manifest, "snapshot": {"error": app.get("error", "not loaded")}}
            continue
        snap = safe_call(app, "snapshot", default={"error": "no snapshot"})
        if not isinstance(snap, dict):
            snap = {"error": "snapshot must be an object"}
        snap.setdefault("counts", {})
        out[app_id] = {"manifest": manifest, "snapshot": snap}
    return out


def get(brain, app_id: str, runtime=None):
    app = load(brain, runtime).get(app_id)
    if app is None:
        raise ValueError(f"Unknown module app: {app_id}")
    if isinstance(app, dict):
        raise ValueError(f"Module app {app_id} failed to load: {app.get('error')}")
    return app


def routines(brain, runtime=None) -> list[dict]:
    """Every module routine, flattened: {app, id, name, every|at, workflow}."""
    out = []
    for app_id, app in load(brain, runtime).items():
        if isinstance(app, dict):
            continue
        items = safe_call(app, "routines", default=[])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", str(item.get("id", ""))):
                continue
            out.append({"app": app_id, "id": item["id"], "name": str(item.get("name") or item["id"])[:160],
                        "every": item.get("every"), "at": item.get("at"), "agent": item.get("agent"),
                        "workflow": f"module:{app_id}:{item['id']}"})
    return out


def interval_seconds(routine: dict) -> int | None:
    every, at = routine.get("every"), routine.get("at")
    if isinstance(every, str) and re.fullmatch(r"\d+[mhd]", every):
        number, unit = int(every[:-1]), every[-1]
        seconds = number * {"m": 60, "h": 3600, "d": 86400}[unit]
        return max(60, min(seconds, 31536000))
    if isinstance(at, str) and re.fullmatch(r"\d{2}:\d{2}", at):
        return 86400
    return None


def first_run(routine: dict, now: float | None = None) -> float:
    now = time.time() if now is None else now
    at = routine.get("at")
    if isinstance(at, str) and re.fullmatch(r"\d{2}:\d{2}", at):
        hour, minute = int(at[:2]), int(at[3:])
        local = datetime.fromtimestamp(now)
        target = local.replace(hour=min(hour, 23), minute=min(minute, 59), second=0, microsecond=0)
        if target.timestamp() <= now:
            target += timedelta(days=1)
        return target.timestamp()
    # An interval routine runs a minute after it is first scheduled, then on its interval: a new
    # daily app should show what it does today, not tomorrow.
    return now + 60


def parse_workflow(workflow: str) -> tuple[str, str] | None:
    parts = str(workflow).split(":")
    if len(parts) == 3 and parts[0] == "module":
        return parts[1], parts[2]
    return None


def agents(brain, runtime=None) -> list[dict]:
    out = []
    for app_id, app in load(brain, runtime).items():
        if isinstance(app, dict):
            continue
        items = safe_call(app, "agents", default=[])
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out.append({**item, "id": f"{app_id}:{item['id']}", "app": app_id, "adapter": "module",
                            "role": item.get("role", ""), "skills": item.get("skills", []), "availability": "module",
                            "state": item.get("state", "ready"), "edits": False,
                            "last_run": item.get("last_run"), "last_error": item.get("last_error")})
    return out


def usage(brain, runtime=None) -> dict:
    out = {}
    for app_id, app in load(brain, runtime).items():
        if isinstance(app, dict):
            continue
        value = safe_call(app, "usage", default={})
        if isinstance(value, dict):
            out[app_id] = value
    return out


def doctor(brain, runtime=None) -> list[dict]:
    rows = []
    for app_id, app in load(brain, runtime).items():
        if isinstance(app, dict):
            rows.append({"status": "FAIL", "name": f"app {app_id}", "detail": app.get("error", "")})
            continue
        items = safe_call(app, "doctor", default=[])
        if isinstance(items, dict):
            rows.append({"status": "FAIL", "name": f"app {app_id}", "detail": items.get("error", "")})
        elif isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    rows.append({"status": str(item.get("status", "info")), "name": f"{app_id}: {item.get('name', '')}",
                                 "detail": str(item.get("detail", ""))})
    return rows


def static(brain, app_id: str, relative: str) -> tuple[bytes, str] | None:
    """A file under apps/<id>/web/, or None. Never anything else."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", app_id) or app_id not in manifests(brain.catalog()):
        return None
    root = (brain.root / "apps" / app_id / "web").resolve()
    parts = [p for p in relative.split("/") if p]
    if len(parts) < 2 or parts[0] != "web" or any(p in {".", ".."} or p.startswith(".") for p in parts):
        return None
    path = root.joinpath(*parts[1:])
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root) or not resolved.is_file() or path.is_symlink():
        return None
    mime = MIME.get(resolved.suffix.lower())
    if mime is None or resolved.stat().st_size > 5_000_000:
        return None
    return resolved.read_bytes(), mime


def describe(result) -> str:
    try:
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]
    except (TypeError, ValueError):
        return str(result)[:2000]
