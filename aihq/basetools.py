"""The tools every OS agent gets, independent of any app.

Reads are repo-scoped and deny secrets. Writes land under hq/ only (the Git brain); the guard
is the path itself, not trust in the caller. Agent-app operations (create, update, delete, run)
are delegated to callables the host injects, so this module does not depend on agentapps.py.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime
from pathlib import Path

from . import core

def search_scopes() -> tuple[str, ...]:
    """Repo folders search_repo covers when no scope is given. Per-checkout config can name
    them (`search_scopes` in curator.json); a generic checkout searches the docs and hq
    folders that exist everywhere."""
    extra = core.local_config().get("search_scopes")
    if isinstance(extra, list) and all(isinstance(x, str) for x in extra):
        return tuple(extra)
    return ("docs", "hq", "vault", "os")
DENY_PARTS = ("looks/studio/codex_outputs",)
DENY_NAMES = re.compile(r"(^\.env|\.pem$|\.key$|secrets|credentials|id_rsa|\.sqlite3?$|\.db$)", re.IGNORECASE)
MAX_READ = 200_000
MAX_FETCH = 1_000_000
FETCH_TEXT = 40_000
INBOX_KINDS = {"approval", "ruling", "proposal", "note"}
APP_OPS = ("create_app", "update_app", "delete_app", "run_app")
EM_DASH, EN_DASH = "\u2014", "\u2013"  # the vault rule: these never appear in anything the OS writes


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


def frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            value = value.strip()
            try:
                meta[key.strip()] = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                meta[key.strip()] = value.strip('"')
    return meta, text[end + 5:]


def render(meta: dict, body: str) -> str:
    lines = ["---"] + [f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in meta.items()] + ["---", ""]
    return "\n".join(lines) + str(body).rstrip() + "\n"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")[:80] or "item"


def clean(text: str) -> str:
    """One line, no em dashes. The vault rule applies to everything the OS writes."""
    return " ".join(str(text).split()).replace(EM_DASH, ",").replace(EN_DASH, "-")


def no_dashes(text: str) -> str:
    return str(text).replace(EM_DASH, ",").replace(EN_DASH, "-")


def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr|section|article)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n", raw)
    return raw.strip()


def _private_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return True
        if address.is_loopback or address.is_private or address.is_link_local or address.is_reserved or address.is_multicast:
            return True
    return False


def fetch_url(url: str, timeout: int = 20, allow_local: bool = False) -> str:
    """GET a public http(s) page as text. 1 MB read cap, 40k characters returned."""
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "fetch_url: only http(s) URLs with a host."
    if not allow_local and _private_host(parsed.hostname):
        return "fetch_url: that host is local or private; refused."
    request = urllib.request.Request(url, headers={"User-Agent": "InfiniteArch-AgenticOS/1.0 (+local)", "Accept": "text/html,application/json,text/plain;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            kind = (response.headers.get("Content-Type") or "").lower()
            raw = response.read(MAX_FETCH + 1)
    except urllib.error.HTTPError as error:
        return f"fetch_url: HTTP {error.code} for {url}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        return f"fetch_url: could not reach {url} ({error})"
    truncated = len(raw) > MAX_FETCH
    text = raw[:MAX_FETCH].decode("utf-8", errors="replace")
    if "html" in kind or text.lstrip()[:1] == "<":
        text = strip_html(text)
    text = text.strip()
    if len(text) > FETCH_TEXT:
        text = text[:FETCH_TEXT] + "\n[cut at 40,000 characters]"
    elif truncated:
        text += "\n[cut at 1 MB]"
    return text or "(empty page)"


class BaseToolbelt:
    """OS-wide tools. `app_id` scopes write_record's default app and the log line's agent name.
    `app_ops` is a dict of callables for create_app/update_app/delete_app/run_app, injected by the
    host; without it those tools answer "not available in this context"."""

    def __init__(self, domain, app_id: str | None = None, app_ops: dict | None = None, agent: str | None = None,
                 allow_local_fetch: bool = False, os_root=None):
        self.domain = Path(domain).resolve()
        self.hq = self.domain / "hq"
        self.data = self.hq / "os"
        self.app_id = app_id
        self.agent = agent or app_id or "os"
        self.app_ops = app_ops or {}
        self.allow_local_fetch = allow_local_fetch
        self.os_root = Path(os_root).resolve() if os_root else self.domain / "os"
        self.calls: list[dict] = []

    # guards -----------------------------------------------------------------
    def _writable(self, path: Path) -> Path:
        resolved = (self.domain / path).resolve() if not path.is_absolute() else path.resolve()
        if resolved != self.hq and self.hq not in resolved.parents:
            raise PermissionError(f"The OS writes only under hq/: refused {resolved}")
        return resolved

    def _readable(self, rel: str) -> Path:
        candidate = (self.domain / str(rel)).resolve()
        if candidate != self.domain and self.domain not in candidate.parents:
            raise PermissionError("Outside the repo.")
        posix = candidate.relative_to(self.domain).as_posix()
        if any(posix.startswith(part) for part in DENY_PARTS) or DENY_NAMES.search(candidate.name) or ".git/" in posix + "/":
            raise PermissionError("That file is not readable by agents.")
        return candidate

    # specs -------------------------------------------------------------------
    def specs(self) -> list[dict]:
        def spec(name, description, properties, required=()):
            return {"name": name, "description": description,
                    "input_schema": {"type": "object", "properties": properties, "required": list(required)}}
        s = {"type": "string"}
        specs = [
            spec("read_file", "Read a file in the repo (governance, hq, shows, docs, code). Secrets are not readable.", {"path": s}, ["path"]),
            spec("search_repo", "Grep the repo for a phrase. Returns path:line: text matches.", {"query": s, "scope": {**s, "description": "optional top-level folder"}}, ["query"]),
            spec("memory", "Read one memory key, or all keys when omitted.", {"key": s}),
            spec("write_memory", "Write or replace a memory key under hq/os/memory.", {"key": s, "content": s}, ["key", "content"]),
            spec("list_inbox", "Open inbox items waiting on the owner.", {"status": {**s, "description": "open (default), approved, rejected, done"}}),
            spec("inbox", "Add an inbox item for the owner. kind: ruling, proposal or note (approvals come only from gated tools).", {"kind": s, "title": s, "body": s}, ["kind", "title", "body"]),
            spec("log", "Append one line to today's hq/log: what happened and where the evidence is.", {"what": s, "evidence": s}, ["what"]),
            spec("fetch_url", "GET a public web page and return its text (40k chars max).", {"url": s}, ["url"]),
            spec("records", "Records of an app's board under hq/os/records/<app>/. Optional substring filter over all fields.", {"app": s, "filter": s}, ["app"]),
            spec("write_record", "Write or update one record on an app's board. fields is an object; include a title.", {"app": s, "id": s, "fields": {"type": "object"}}, ["id", "fields"]),
            spec("list_apps", "Every app the OS knows: module apps (code) and agent apps (data under hq/os/apps).", {}),
            spec("create_app", "Create an agent app: a standing prompt that runs on a schedule and fills a board. id is kebab-case.",
                 {"id": s, "name": s, "description": s, "prompt": {**s, "description": "the standing instructions"},
                  "every": {**s, "description": "like 1d, 6h, 30m (or use at)"}, "at": {**s, "description": "HH:MM local, daily"},
                  "fields": {"type": "array", "items": s}, "key": {**s, "description": "the field that identifies a record, like url"},
                  "web": {"type": "boolean"}, "notify": {"type": "boolean"}, "model": {**s, "description": "default or deep"}},
                 ["id", "name", "prompt"]),
            spec("update_app", "Change an agent app's prompt, schedule, fields or flags.", {"id": s, "changes": {"type": "object"}}, ["id", "changes"]),
            spec("delete_app", "Retire an agent app (moved aside, not destroyed).", {"id": s}, ["id"]),
            spec("run_app", "Run an agent app's routine now.", {"id": s}, ["id"]),
        ]
        return specs

    # dispatch -----------------------------------------------------------------
    def call(self, name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        handler = getattr(self, "tool_" + name, None)
        if handler is None:
            return f"Unknown tool: {name}"
        try:
            result = handler(**args)
        except TypeError as exc:
            result = f"{name}: bad arguments ({exc})"
        except PermissionError as exc:
            result = f"{name}: refused ({exc})"
        except (OSError, ValueError) as exc:
            result = f"{name}: failed ({exc})"
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=1, default=str)
        self.calls.append({"name": name, "args": args, "result": text[:4000]})
        return text

    # reads --------------------------------------------------------------------
    def tool_read_file(self, path: str) -> str:
        target = self._readable(path)
        if not target.is_file():
            return f"No such file: {path}"
        if target.stat().st_size > MAX_READ * 4:
            return f"{path} is large; read a part of it with search_repo instead."
        text = target.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_READ] + ("\n[cut at 200,000 characters]" if len(text) > MAX_READ else "")

    def tool_search_repo(self, query: str, scope: str | None = None, limit: int = 40) -> str:
        needle = str(query).lower()
        if not needle:
            return "search_repo: empty query"
        scopes = [scope] if scope else list(search_scopes())
        hits = []
        for name in scopes:
            root = self.domain / name
            paths = [root] if root.is_file() else (sorted(root.rglob("*")) if root.is_dir() else [])
            for path in paths:
                if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".rs", ".toml", ".csv"}:
                    continue
                try:
                    self._readable(str(path.relative_to(self.domain)))
                except PermissionError:
                    continue
                if path.stat().st_size > 2_000_000:
                    continue
                try:
                    for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                        if needle in line.lower():
                            hits.append(f"{path.relative_to(self.domain).as_posix()}:{number}: {line.strip()[:200]}")
                            if len(hits) >= limit:
                                return "\n".join(hits)
                except OSError:
                    continue
        return "\n".join(hits) or "No matches."

    # memory --------------------------------------------------------------------
    def memories(self) -> list[dict]:
        root = self.data / "memory"
        out = []
        if root.is_dir():
            for path in sorted(root.glob("*.md")):
                meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                out.append({"key": str(meta.get("key") or path.stem), "content": body.strip(), "updated": float(meta.get("updated", 0) or 0)})
        return out

    def memory_digest(self, limit_chars: int = 6000) -> str:
        parts, used = [], 0
        for item in self.memories():
            chunk = f"## {item['key']}\n{item['content']}\n"
            if used + len(chunk) > limit_chars:
                remaining = limit_chars - used
                if remaining > 200:
                    parts.append(chunk[:remaining] + "\n[memory cut]\n")
                break
            parts.append(chunk)
            used += len(chunk)
        return "\n".join(parts)

    def tool_memory(self, key: str | None = None):
        items = self.memories()
        if key:
            wanted = slug(key)
            for item in items:
                if slug(item["key"]) == wanted:
                    return item["content"]
            return f"No memory named {key}. Keys: " + ", ".join(i["key"] for i in items)
        return "\n\n".join(f"## {i['key']}\n{i['content']}" for i in items) or "Memory is empty."

    def tool_write_memory(self, key: str, content: str) -> str:
        key = clean(key)[:120]
        if not key or not str(content).strip():
            raise ValueError("Memory needs a key and some content.")
        path = self._writable(self.data / "memory" / f"{slug(key)}.md")
        _atomic_write(path, render({"key": key, "updated": time.time()}, no_dashes(content)))
        return f"memory written: {key}"

    # inbox ----------------------------------------------------------------------
    def inbox_items(self, status: str | None = "open") -> list[dict]:
        root = self.data / "inbox"
        out = []
        if root.is_dir():
            for path in sorted(root.glob("*.md")):
                meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                if status and meta.get("status") != status:
                    continue
                out.append({**meta, "body": body.strip()})
        out.sort(key=lambda item: str(item.get("created", "")), reverse=True)
        return out

    def add_inbox(self, kind: str, title: str, body: str, **extra) -> dict:
        kind, title = clean(kind)[:40], clean(title)[:200]
        if not kind or not title:
            raise ValueError("Inbox items need a kind and a title.")
        id = f"{date.today():%Y%m%d}-{uuid.uuid4().hex[:8]}"
        meta = {"id": id, "kind": kind, "title": title, "status": "open",
                "created": datetime.now().isoformat(timespec="seconds"), "decided": "", "note": "",
                "agent": extra.pop("agent", self.agent)}
        for key, value in extra.items():
            if key not in meta:
                meta[key] = value
        _atomic_write(self._writable(self.data / "inbox" / f"{id}.md"), render(meta, no_dashes(body or "")))
        return {**meta, "body": str(body or "")}

    def tool_list_inbox(self, status: str = "open"):
        items = self.inbox_items(status or "open")
        return [{"id": i.get("id"), "kind": i.get("kind"), "title": i.get("title"), "created": i.get("created"), "app": i.get("app", "")} for i in items] or "Inbox is empty."

    def tool_inbox(self, kind: str, title: str, body: str) -> str:
        if kind not in INBOX_KINDS - {"approval"}:
            return "inbox: kind must be ruling, proposal or note."
        item = self.add_inbox(kind, title, body, app=self.app_id or "")
        return f"inbox item {item['id']} created"

    # log ----------------------------------------------------------------------------
    def log(self, what: str, evidence: str = "", agent: str | None = None) -> None:
        path = self._writable(self.hq / "log" / f"{date.today():%Y-%m-%d}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        line = f"- {datetime.now():%H:%M} · {agent or self.agent} · {clean(what)[:300]} · {clean(evidence)[:200] or 'hq/os'}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def tool_log(self, what: str, evidence: str = "") -> str:
        self.log(what, evidence)
        return "logged"

    # web -------------------------------------------------------------------------------
    def tool_fetch_url(self, url: str) -> str:
        return fetch_url(url, allow_local=self.allow_local_fetch)

    # records -----------------------------------------------------------------------------
    def records_dir(self, app: str) -> Path:
        return self.data / "records" / slug(app)

    def records(self, app: str, filter: str | None = None) -> list[dict]:
        root = self.records_dir(app)
        out = []
        needle = (filter or "").lower()
        if root.is_dir():
            for path in sorted(root.glob("*.md")):
                meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                item = {**meta, "body": body.strip()}
                if needle and needle not in json.dumps(item, ensure_ascii=False, default=str).lower():
                    continue
                out.append(item)
        out.sort(key=lambda item: str(item.get("updated", item.get("created", ""))), reverse=True)
        return out

    def tool_records(self, app: str, filter: str | None = None):
        items = self.records(app, filter)
        return items or f"No records for {app}."

    def write_record(self, app: str, id: str, fields: dict) -> dict:
        if not isinstance(fields, dict):
            raise ValueError("fields must be an object")
        rid = slug(id)
        path = self._writable(self.records_dir(app) / f"{rid}.md")
        existing, _ = frontmatter(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else ({}, "")
        meta = {k: v for k, v in existing.items() if k not in ("body",)}
        for key, value in fields.items():
            if key == "note":
                continue
            meta[str(key)] = no_dashes(value) if isinstance(value, str) else value
        meta["id"] = rid
        meta["app"] = slug(app)
        meta.setdefault("created", datetime.now().isoformat(timespec="seconds"))
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        body = no_dashes(fields.get("note", "") or "")
        if not body and path.is_file():
            body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))[1]
        _atomic_write(path, render(meta, body))
        return {**meta, "body": body.strip(), "new": not existing}

    def tool_write_record(self, id: str, fields: dict, app: str | None = None) -> str:
        app = app or self.app_id
        if not app:
            return "write_record: say which app's board (app)."
        item = self.write_record(app, id, fields)
        return f"record {'created' if item['new'] else 'updated'}: {app}/{item['id']}"

    # apps --------------------------------------------------------------------------------
    def tool_list_apps(self):
        out = []
        for root, kind_default in ((self.data / "apps", "agent"), (self.os_root / "apps", "module")):
            if not root.is_dir():
                continue
            for manifest in sorted(root.glob("*/app.json")):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                out.append({"id": data.get("id", manifest.parent.name), "name": data.get("name", ""), "kind": data.get("kind", kind_default),
                            "description": str(data.get("description", ""))[:200], "every": data.get("every"), "at": data.get("at")})
        return out or "No apps."

    def _app_op(self, op_name: str, **args):
        op = self.app_ops.get(op_name)
        if op is None:
            return f"{op_name}: not available in this context."
        result = op(**args)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)

    def tool_create_app(self, **args):
        return self._app_op("create_app", **args)

    def tool_update_app(self, id: str, changes: dict):
        return self._app_op("update_app", id=id, changes=changes)

    def tool_delete_app(self, id: str):
        return self._app_op("delete_app", id=id)

    def tool_run_app(self, id: str):
        return self._app_op("run_app", id=id)
