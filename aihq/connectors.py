"""Connectors: how agents reach the outside world. Built-ins plus any MCP server.

`registry(domain)` returns every connector by id. Built-in ids: gmail, calendar, telegram,
instagram, site. MCP servers from hq/os/connectors.json get ids `mcp.<server id>`.

Policy keys and Activity use dotted names (`gmail.send`, `mcp.notion.search`). Tool names sent to
a model use `__` instead of dots (`gmail__send`), because model APIs only accept
[a-zA-Z0-9_-] in tool names. `ConnectorTools.call` accepts either form.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

from . import activity, core, mcp
from .builtins import ALL as BUILTINS
from .builtins.base import Connector

CONFIG = Path("hq") / "os" / "connectors.json"
SEP = "__"
BASE_ASK = ("print", "photos", "push", "governance")


def always_ask() -> re.Pattern:
    """Terms that can never run automatically: the built-in set plus this checkout's
    `always_ask_terms` in curator.json (generic installs have neither file nor terms)."""
    terms = list(BASE_ASK) + [t for t in core.local_config().get("always_ask_terms", [])
                              if isinstance(t, str) and t.strip()]
    return re.compile("|".join(re.escape(t.strip()) for t in terms), re.IGNORECASE)
MCP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


# ---------- config ----------

def load_config(domain: Path) -> dict:
    try:
        data = json.loads((Path(domain) / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("mcp", [])
    data.setdefault("builtin", {})
    return data


def save_config(domain: Path, data: dict) -> None:
    path = Path(domain) / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temp = Path(handle.name)
    temp.replace(path)


def _no_plain_secrets(mapping: dict, what: str) -> dict:
    out = {}
    for key, value in (mapping or {}).items():
        key, value = str(key), str(value)
        if value and not value.startswith("secret:") and re.search(r"(token|key|secret|password|auth)", key, re.IGNORECASE):
            raise ValueError(f"{what} {key} looks like a secret: save it under Connectors and use secret:<name> here.")
        out[key] = value
    return out


def add_mcp(domain: Path, spec: dict) -> dict:
    sid = str(spec.get("id", "")).strip().lower()
    if not MCP_ID.match(sid):
        raise ValueError("An MCP server id is lowercase letters, digits and dashes.")
    transport = spec.get("transport", "stdio")
    if transport not in ("stdio", "http"):
        raise ValueError("transport is stdio or http.")
    entry = {"id": sid, "name": str(spec.get("name") or sid), "transport": transport, "enabled": bool(spec.get("enabled", True))}
    if transport == "stdio":
        if not spec.get("command"):
            raise ValueError("A stdio server needs a command (npx, uvx, a path).")
        entry["command"] = str(spec["command"])
        entry["args"] = [str(a) for a in spec.get("args") or []]
        entry["env"] = _no_plain_secrets(spec.get("env") or {}, "env")
    else:
        url = str(spec.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise ValueError("An http server needs an http(s) url.")
        entry["url"] = url
        entry["headers"] = _no_plain_secrets(spec.get("headers") or {}, "header")
    data = load_config(domain)
    data["mcp"] = [m for m in data["mcp"] if m.get("id") != sid] + [entry]
    save_config(domain, data)
    return entry


def remove_mcp(domain: Path, sid: str) -> bool:
    data = load_config(domain)
    before = len(data["mcp"])
    data["mcp"] = [m for m in data["mcp"] if m.get("id") != sid]
    save_config(domain, data)
    return len(data["mcp"]) < before


def update(domain: Path, cid: str, patch: dict) -> dict:
    """Patch a built-in's settings (`gmail`, `telegram`, ...) or an MCP server (`mcp.<id>` or `<id>`)."""
    data = load_config(domain)
    patch = {k: v for k, v in (patch or {}).items() if k not in {"id"}}
    if cid in BUILTINS:
        current = dict(data["builtin"].get(cid) or {})
        strings = {k: v for k, v in patch.items() if isinstance(v, str)}
        _no_plain_secrets(strings, "setting")  # raises on a pasted token; values keep their types
        current.update(patch)
        data["builtin"][cid] = current
        save_config(domain, data)
        return current
    sid = cid[4:] if cid.startswith("mcp.") else cid
    for entry in data["mcp"]:
        if entry.get("id") == sid:
            for key in ("name", "enabled", "args", "command", "url"):
                if key in patch:
                    entry[key] = patch[key]
            if "env" in patch:
                entry["env"] = _no_plain_secrets(patch["env"], "env")
            if "headers" in patch:
                entry["headers"] = _no_plain_secrets(patch["headers"], "header")
            save_config(domain, data)
            return entry
    raise ValueError(f"No connector {cid}.")


# ---------- MCP as a connector ----------

class MCPConnector(Connector):
    kind = "mcp"

    def __init__(self, domain: Path, spec: dict):
        super().__init__(domain, spec)
        self.spec = spec
        self.server_id = spec["id"]
        self.id = f"mcp.{spec['id']}"
        self.name = spec.get("name") or spec["id"]
        self.description = f"MCP server ({spec.get('transport', 'stdio')}): " + (
            " ".join([spec.get("command", "")] + list(spec.get("args") or [])) if spec.get("transport", "stdio") == "stdio" else spec.get("url", ""))
        self._tools: list[dict] | None = None
        self.ACTIONS = set()

    def _missing(self) -> list[str]:
        from . import secrets
        refs = list((self.spec.get("env") or {}).values()) + list((self.spec.get("headers") or {}).values())
        return [r[7:] for r in refs if isinstance(r, str) and r.startswith("secret:") and not secrets.present(r[7:])]

    def status(self) -> dict:
        if not self.spec.get("enabled", True):
            return {"ok": False, "needs": [], "detail": "Disabled."}
        missing = self._missing()
        if missing:
            return {"ok": False, "needs": missing, "detail": "Needs " + ", ".join(missing) + " (Connectors > " + self.name + ")."}
        return {"ok": True, "needs": [], "detail": "configured; tools load on first use"}

    def _client(self):
        return mcp.client(self.spec)

    def tools(self) -> list[dict]:
        if self._tools is None:
            raw = self._client().list_tools()
            self._tools, self.ACTIONS = [], set()
            for tool in raw:
                name = str(tool.get("name", ""))
                if not name:
                    continue
                if not (tool.get("annotations") or {}).get("readOnlyHint"):
                    self.ACTIONS.add(name)
                self._tools.append({"name": name, "description": str(tool.get("description", ""))[:900],
                                    "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}}})
        return self._tools

    def call(self, tool: str, args: dict) -> str:
        return self._client().call_tool(tool, args or {})


# ---------- the registry ----------

def registry(domain: Path) -> dict[str, Connector]:
    domain = Path(domain)
    data = load_config(domain)
    out: dict[str, Connector] = {}
    for cid, cls in BUILTINS.items():
        settings = dict(data["builtin"].get(cid) or {})
        out[cid] = cls(domain, settings)
    for spec in data["mcp"]:
        if isinstance(spec, dict) and MCP_ID.match(str(spec.get("id", ""))):
            out[f"mcp.{spec['id']}"] = MCPConnector(domain, spec)
    return out


def describe(domain: Path, load_tools: bool = False) -> list[dict]:
    """For the Connectors panel. MCP tool lists only load when asked (they start the server)."""
    rows = []
    for connector in registry(domain).values():
        status = connector.status()
        row = {"id": connector.id, "name": connector.name, "kind": connector.kind, "description": connector.description,
               "status": status, "enabled": bool(connector.config.get("enabled", True)), "tools": []}
        if status.get("ok") and (connector.kind == "builtin" or load_tools):
            try:
                row["tools"] = [{"name": t["name"], "description": t.get("description", ""), "action": t["name"] in connector.ACTIONS}
                                for t in connector.tools()]
            except Exception as exc:
                row["status"] = {"ok": False, "needs": [], "detail": f"could not list tools: {exc}"}
        rows.append(row)
    return rows


# ---------- policy ----------

def policy_for(policy: dict, dotted: str) -> str:
    """auto or ask. Exact name, then `<connector>.*`, then `*`; default ask. Some things always ask."""
    if always_ask().search(dotted):
        return "ask"
    policy = policy or {}
    connector = dotted.rsplit(".", 1)[0]
    for key in (dotted, f"{connector}.*", "*"):
        value = policy.get(key)
        if value in ("auto", "ask"):
            return value
    return "ask"


def _dotted(name: str) -> str:
    return name.replace(SEP, ".")


def _summarize(args: dict, limit: int = 200) -> str:
    parts = []
    for key in ("title", "subject", "to", "text", "caption", "start", "query", "path"):
        if args.get(key):
            parts.append(f"{key}: {str(args[key])[:80]}")
    return ("; ".join(parts) or json.dumps(args, ensure_ascii=False, default=str))[:limit]


class ConnectorTools:
    """A toolbelt over the connectors a bot or chat may use, honoring policy and recording Activity."""

    def __init__(self, domain: Path, allowed="all", policy: dict | None = None, who: str = "operator", runtime=None):
        self.domain = Path(domain)
        self.policy = dict(policy or {})
        self.who = who
        self.runtime = runtime
        self.calls: list[dict] = []
        every = registry(self.domain)
        if allowed == "all" or allowed is None:
            chosen = every
        else:
            chosen = {cid: c for cid, c in every.items() if cid in set(allowed)}
        self.connectors = {cid: c for cid, c in chosen.items() if c.status().get("ok") and c.config.get("enabled", True) is not False}
        self._specs: list[dict] | None = None
        self._index: dict[str, tuple[Connector, str]] = {}

    def specs(self) -> list[dict]:
        if self._specs is not None:
            return self._specs
        out = []
        for cid, connector in self.connectors.items():
            try:
                tools = connector.tools()
            except Exception:
                continue  # a broken server must not take the rest down
            prefix = cid.replace(".", SEP)
            for tool in tools:
                api_name = f"{prefix}{SEP}{tool['name']}"
                if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", api_name):
                    continue
                dotted = f"{cid}.{tool['name']}"
                mode = policy_for(self.policy, dotted) if tool["name"] in connector.ACTIONS else "read"
                note = {"read": "", "auto": " [acts now]", "ask": f" [asks {core.owner_name()} first]"}[mode]
                out.append({"name": api_name, "description": (tool.get("description", "") + note)[:1000],
                            "input_schema": tool.get("input_schema") or {"type": "object", "properties": {}}})
                self._index[api_name] = (connector, tool["name"])
        self._specs = out
        return out

    def _lookup(self, name: str):
        self.specs()
        if name in self._index:
            return self._index[name]
        alt = name.replace(".", SEP)
        return self._index.get(alt)

    def call(self, name: str, args: dict) -> str:
        found = self._lookup(name)
        if found is None:
            return f"Unknown tool: {name}"
        connector, tool = found
        args = args if isinstance(args, dict) else {}
        dotted = f"{connector.id}.{tool}"
        if tool not in connector.ACTIONS:
            try:
                return connector.call(tool, args)
            except Exception as exc:
                return f"{dotted} failed: {exc}"
        if policy_for(self.policy, dotted) == "ask":
            item = queue_approval(self.domain, connector.id, tool, args, self.who)
            self.calls.append({"name": dotted, "args": args, "status": "queued", "id": item["id"]})
            owner = core.owner_name()
            return f"QUEUED for {owner}'s approval: {item['id']} ({dotted}). Do not retry; tell {owner} it is waiting."
        try:
            result = connector.call(tool, args)
            activity.record(self.domain, self.who, connector.id, tool, _summarize(args), result, "done")
            self.calls.append({"name": dotted, "args": args, "status": "done"})
            return result
        except Exception as exc:
            activity.record(self.domain, self.who, connector.id, tool, _summarize(args), str(exc), "failed")
            self.calls.append({"name": dotted, "args": args, "status": "failed"})
            return f"{dotted} failed: {exc}"


# ---------- approvals ----------

def _inbox_dir(domain: Path) -> Path:
    return Path(domain) / "hq" / "os" / "inbox"


def _render(meta: dict, body: str) -> str:
    return "\n".join(["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", ""]) + body.rstrip() + "\n"


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            try:
                meta[key.strip()] = json.loads(value.strip())
            except ValueError:
                meta[key.strip()] = value.strip().strip('"')
    return meta, text[end + 5:]


def queue_approval(domain: Path, connector: str, tool: str, args: dict, who: str) -> dict:
    """An inbox approval in the Git-brain store's format: kind approval, action connector.call."""
    item_id = f"{date.today():%Y%m%d}-{uuid.uuid4().hex[:8]}"
    title = f"{who.replace('app:', '').replace('-', ' ').title() if who.startswith('app:') else who.title()} wants to {connector}.{tool}: {_summarize(args, 90)}"
    meta = {"id": item_id, "kind": "approval", "title": title[:200], "status": "open",
            "created": datetime.now().isoformat(timespec="seconds"), "decided": "", "note": "", "agent": who,
            "action": "connector.call", "connector": connector, "tool": tool}
    payload = json.dumps({"connector": connector, "tool": tool, "args": args}, indent=2, ensure_ascii=False, default=str)
    body = f"{who} wants to run {connector}.{tool}. Approve to run it once.\n\n```json\n{payload}\n```"
    folder = _inbox_dir(domain)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item_id}.md").write_text(_render(meta, body.replace("\u2014", ", ")), encoding="utf-8")
    activity.record(domain, who, connector, tool, _summarize(args), f"waiting for approval {item_id}", "queued")
    return {**meta, "body": body}


def _parse_payload(body: str) -> dict:
    match = re.search(r"```json\s*\n(.*?)\n```", body or "", re.S)
    if not match:
        raise ValueError("The approval has no connector call in it.")
    data = json.loads(match.group(1))
    if not isinstance(data, dict) or not data.get("connector") or not data.get("tool"):
        raise ValueError("The approval's connector call is incomplete.")
    return data


def execute_approved(domain: Path, item) -> dict:
    """Run a queued connector.call once (item is an id or an inbox item dict), mark it approved."""
    domain = Path(domain)
    item_id = item if isinstance(item, str) else item.get("id")
    path = _inbox_dir(domain) / f"{item_id}.md"
    if not path.is_file():
        raise ValueError(f"No inbox item {item_id}.")
    meta, body = _frontmatter(path.read_text(encoding="utf-8"))
    if meta.get("action") != "connector.call":
        raise ValueError("That item is not a connector call.")
    if meta.get("status") != "open":
        raise ValueError(f"Already {meta.get('status')}; a connector call runs at most once.")
    call = _parse_payload(body)
    connector = registry(domain).get(call["connector"])
    who = str(meta.get("agent") or core.owner_id())
    meta["status"], meta["decided"] = "approved", datetime.now().isoformat(timespec="seconds")
    path.write_text(_render(meta, body), encoding="utf-8")  # mark first: a crash mid-call never runs it twice
    if connector is None:
        result, status = f"connector {call['connector']} is not configured any more", "failed"
    else:
        try:
            result, status = connector.call(call["tool"], call.get("args") or {}), "done"
        except Exception as exc:
            result, status = f"failed: {exc}", "failed"
    meta["note"] = str(result).replace("\u2014", ", ")[:500]
    path.write_text(_render(meta, body), encoding="utf-8")
    activity.record(domain, who, call["connector"], call["tool"], _summarize(call.get("args") or {}), result, status)
    return {"ok": status == "done", "id": item_id, "result": str(result)[:2000], "status": status}
