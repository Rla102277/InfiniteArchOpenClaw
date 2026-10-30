"""Agent apps: apps defined by data, not code (system/contracts/AGENT_RUNTIME.md).

An agent app is a folder in the brain, `hq/os/apps/<id>/`, holding `app.json` (name, cadence,
output fields, key, web, notify, model) and `prompt.md` (the standing instructions). The OS runs
it on its schedule: the prompt plus the repo rules, memory and the records it already has go to
the model with the base tools and a scoped `write_record`; new or changed records become one
inbox note. Chatting with an app is the same prompt with the thread's history.

Everything lands under `hq/`: records in `hq/os/records/<id>/`, runs in `hq/os/runs/`, notes in
`hq/os/inbox/`, one line per run in `hq/log/`. Creating an app is writing two files, so the
Operator can do it from a chat (`create_app`) and brain sync carries it to GitHub.

The model layer (`llm`), the loop (`agentrun`), threads (`chats`) and the shared tools
(`basetools`) are imported lazily and can be injected, so this module loads and tests without
them.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from . import core

SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}")
FIELD = re.compile(r"[a-z][a-z0-9_]{0,31}")
MODELS = {"default", "deep"}
APPS = "hq/os/apps"
RECORDS = "hq/os/records"
INBOX = "hq/os/inbox"
RUNS = "hq/os/runs"
MEMORY = "hq/os/memory"
LOG = "hq/log"
DELETED = "_deleted"
MAX_RECORDS_IN_CONTEXT = 60
RUN_PROMPT = "Run your routine now. Report what you added or changed."
RULES = """## Rules for this app (the OS enforces the rest)

- Voice: direct, first person, verdict first. No em dashes, ever. No filler.
- Never invent. If a fact (a date, a fee, a name) is not on a page you read, write "unknown".
- Cite the URL you took each fact from; prefer the official source over an aggregator.
- Add or update records with `write_record`; the key field identifies a record, so the same key
  updates, never duplicates. Change a record only when something changed.
- Say plainly when there is nothing new.
- Your records live in hq/os/records/<app>/ and are reviewed by the owner; nothing here prints,
  writes to Photos or touches governance/.
"""
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


# ---------- small helpers (same file format as the module apps' store) ----------

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
    return "\n".join(lines) + str(body or "").rstrip() + "\n"


def no_dashes(text: str) -> str:
    return str(text).replace("—", ",").replace("–", "-")


def clean(text) -> str:
    return " ".join(str(text).split()).replace("—", ",").replace("–", "-")


def slug(value: str) -> str:
    body = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")[:80]
    return body or "item"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(no_dashes(content))
        temporary = Path(handle.name)
    temporary.replace(path)


def _hq(domain: Path) -> Path:
    return (Path(domain) / "hq").resolve()


def _inside(domain: Path, relative: str) -> Path:
    """A path under hq/, or PermissionError. Agent apps never write anywhere else."""
    hq = _hq(domain)
    path = (Path(domain) / relative).resolve()
    if not path.is_relative_to(hq):
        raise PermissionError(f"Agent apps write only under hq/. Refused: {relative}")
    return path


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(no_dashes(line))
        stream.flush()
        os.fsync(stream.fileno())


def log_line(domain: Path, agent: str, what: str, evidence: str) -> None:
    now = datetime.now()
    _append(_inside(domain, f"{LOG}/{now:%Y-%m-%d}.md"),
            f"- {now:%H:%M} · {clean(agent)[:40]} · {clean(what)[:300]} · {clean(evidence)[:200]}\n")


def memory_digest(domain: Path, limit_chars: int = 6000) -> str:
    root = Path(domain) / MEMORY
    if not root.is_dir():
        return ""
    parts = []
    for path in sorted(root.glob("*.md")):
        meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        parts.append(f"### {meta.get('key', path.stem)}\n{body.strip()}")
    text = "\n\n".join(parts)
    return text[:limit_chars] + ("\n[memory truncated]" if len(text) > limit_chars else "")


def add_inbox(domain: Path, kind: str, title: str, body: str, **extra) -> dict:
    id = f"{date.today():%Y%m%d}-{uuid.uuid4().hex[:8]}"
    meta = {"id": id, "kind": clean(kind)[:40], "title": clean(title)[:200], "status": "open",
            "created": datetime.now().isoformat(timespec="seconds"), "decided": "", "note": "",
            "agent": extra.pop("agent", "os")}
    for key, value in extra.items():
        meta.setdefault(key, value)
    _atomic_write(_inside(domain, f"{INBOX}/{id}.md"), render(meta, body))
    return {**meta, "body": str(body or "")}


def save_run(domain: Path, run: dict) -> Path:
    started = str(run.get("started") or datetime.now().isoformat())
    run_id = re.sub(r"[^a-z0-9-]", "", str(run.get("id") or uuid.uuid4().hex).lower()) or uuid.uuid4().hex
    path = _inside(domain, f"{RUNS}/{started[:10]}/{run_id}.json")
    _atomic_write(path, json.dumps(run, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


# ---------- the spec ----------

def validate(spec: dict) -> dict:
    """Normalize and check an app.json. Raises ValueError with the reason."""
    if not isinstance(spec, dict):
        raise ValueError("app.json must be an object.")
    app_id = spec.get("id")
    if not isinstance(app_id, str) or not SLUG.fullmatch(app_id) or app_id == DELETED:
        raise ValueError("App id is lowercase letters, digits and hyphens (start with a letter).")
    if spec.get("kind", "agent") != "agent":
        raise ValueError("Agent apps have kind 'agent'.")
    name = clean(spec.get("name") or "")
    if not 1 <= len(name) <= 80:
        raise ValueError("App name is 1 to 80 characters.")
    description = clean(spec.get("description") or "")[:400]
    icon = str(spec.get("icon") or "◎")
    if not 1 <= len(icon) <= 4:
        raise ValueError("App icon is one to four characters.")
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields or len(fields) > 24:
        raise ValueError("App fields is a list of 1 to 24 field names.")
    fields = [str(f) for f in fields]
    if any(not FIELD.fullmatch(f) for f in fields) or len(set(fields)) != len(fields):
        raise ValueError("Field names are lowercase identifiers (letters, digits, underscore), unique.")
    for reserved in ("status", "note"):
        if reserved not in fields:
            fields.append(reserved)
    key = spec.get("key") or fields[0]
    if key not in fields:
        raise ValueError("App key must be one of its fields.")
    every, at = spec.get("every"), spec.get("at")
    if every is not None and not (isinstance(every, str) and re.fullmatch(r"\d+[mhd]", every)):
        raise ValueError("App cadence `every` is like 15m, 6h or 1d.")
    if at is not None and not (isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at)):
        raise ValueError("App cadence `at` is HH:MM.")
    if every and at:
        raise ValueError("Give `every` or `at`, not both.")
    model = str(spec.get("model") or "default").strip()
    if model not in MODELS and not MODEL_REF.fullmatch(model):
        raise ValueError("App model is default, deep, or a ref like openai:gpt-5 or anthropic:claude-opus-5-5.")
    triggers = _triggers(spec.get("triggers"))
    connectors = spec.get("connectors", [])
    if connectors != "all":
        if not isinstance(connectors, list) or any(not isinstance(c, str) or not CONNECTOR_ID.fullmatch(c) for c in connectors):
            raise ValueError("App connectors is \"all\" or a list of connector ids like calendar, telegram, gmail, mcp.notion.")
        connectors = list(dict.fromkeys(connectors))
    policy = _policy(spec.get("policy"))
    return {"schema_version": 1, "id": app_id, "name": name, "kind": "agent", "icon": icon, "description": description,
            "every": every, "at": at, "fields": fields, "key": key,
            "notify": bool(spec.get("notify", True)), "web": bool(spec.get("web", True)), "model": model,
            "triggers": triggers, "connectors": connectors, "policy": policy}


MODEL_REF = re.compile(r"(anthropic|openai|gemini|openrouter|ollama):[\w./:@-]{1,120}")
CONNECTOR_ID = re.compile(r"[a-z][a-z0-9-]{0,40}(\.[a-z0-9][a-z0-9_-]{0,40})?")
TOOL_REF = re.compile(r"\*|[a-z][a-z0-9-]{0,40}(\.[a-z0-9][a-z0-9_.-]{0,80})?(\.\*)?")
def always_ask() -> re.Pattern:
    """Tool-name terms that always wait for the owner: the built-in set plus this
    checkout's `always_ask_terms` in curator.json."""
    terms = ["print", "photos", "governance"] + [t for t in core.local_config().get("always_ask_terms", [])
                                               if isinstance(t, str) and t.strip()]
    return re.compile(r"(^|\.)(" + "|".join(re.escape(t.strip()) for t in terms) + r")(\.|_|$)")


def _triggers(raw) -> list[dict]:
    if raw in (None, []):
        return []
    if not isinstance(raw, list) or len(raw) > 8:
        raise ValueError("App triggers is a list of up to 8 triggers.")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each trigger is an object with a type.")
        kind = item.get("type")
        every, at = item.get("every"), item.get("at")
        if every is not None and not (isinstance(every, str) and re.fullmatch(r"\d+[mhd]", every)):
            raise ValueError("A trigger's `every` is like 30m, 6h or 1d.")
        if at is not None and not (isinstance(at, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", at)):
            raise ValueError("A trigger's `at` is HH:MM.")
        if kind == "schedule":
            if bool(every) == bool(at):
                raise ValueError("A schedule trigger has exactly one of `every` or `at`.")
            out.append({"type": "schedule", **({"every": every} if every else {"at": at})})
        elif kind == "email":
            query = clean(item.get("query") or "")
            if not query:
                raise ValueError("An email trigger needs a Gmail search `query`.")
            out.append({"type": "email", "query": query[:300], "every": every or "30m"})
        elif kind == "telegram":
            command = str(item.get("command") or "").strip().lower()
            if not re.fullmatch(r"/[a-z][a-z0-9_]{0,31}", command) or command in {"/start", "/today", "/inbox", "/help"}:
                raise ValueError("A telegram trigger's command looks like /scout (and is not /start, /today, /inbox or /help).")
            out.append({"type": "telegram", "command": command})
        else:
            raise ValueError("Trigger type is schedule, email or telegram.")
    return out


def _policy(raw) -> dict:
    if raw in (None, {}):
        return {"*": "ask"}
    if not isinstance(raw, dict):
        raise ValueError("App policy maps tool names to auto or ask.")
    out = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not TOOL_REF.fullmatch(name) or value not in ("auto", "ask"):
            raise ValueError("Policy entries look like \"calendar.create_event\": \"auto\" (auto or ask).")
        out[name] = "ask" if always_ask().search(name) else value
    out.setdefault("*", "ask")
    return out


def policy_for(policy: dict, tool: str) -> str:
    """auto or ask for a namespaced tool: exact name, then <connector>.*, then *, default ask."""
    if always_ask().search(tool):
        return "ask"
    if tool in policy:
        return policy[tool]
    connector = tool.split(".", 1)[0]
    return policy.get(f"{connector}.*") or policy.get("*") or "ask"


# ---------- one app ----------

class AgentApp:
    def __init__(self, domain: Path, spec: dict, prompt: str):
        self.domain = Path(domain).resolve()
        self.spec = validate(spec)
        self.id = self.spec["id"]
        self.prompt = no_dashes(prompt or "").strip()
        self.dir = self.domain / APPS / self.id

    # files ---------------------------------------------------------------------
    @property
    def records_dir(self) -> Path:
        return self.domain / RECORDS / self.id

    def record_path(self, key_value: str) -> Path:
        return _inside(self.domain, f"{RECORDS}/{self.id}/{slug(key_value)}.md")

    def records(self) -> list[dict]:
        root = self.records_dir
        if not root.is_dir():
            return []
        out = []
        for path in sorted(root.glob("*.md")):
            meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if meta.get("id"):
                out.append({**meta, "body": body.strip()})
        out.sort(key=lambda r: str(r.get("updated") or r.get("created") or ""), reverse=True)
        return out

    def record(self, record_id: str) -> dict | None:
        path = self.records_dir / f"{slug(record_id)}.md"
        if not path.is_file():
            return None
        meta, body = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        return {**meta, "body": body.strip()}

    def write_record(self, fields: dict) -> tuple[dict, str]:
        """Add or update by key. Returns (record, "created" | "updated" | "unchanged")."""
        if not isinstance(fields, dict):
            raise ValueError("write_record needs an object of fields.")
        key_name = self.spec["key"]
        key_value = clean(fields.get(key_name) or fields.get("id") or "")
        if not key_value:
            raise ValueError(f"A record needs its key field `{key_name}`.")
        record_id = slug(key_value)
        path = self.record_path(key_value)
        now = datetime.now().isoformat(timespec="seconds")
        previous = self.record(record_id)
        meta = {"id": record_id, "app": self.id, "title": "", "status": "new", "created": now, "updated": now, "source": "agent-app"}
        if previous:
            meta["created"] = previous.get("created", now)
            for field in self.spec["fields"]:
                if field in previous:
                    meta[field] = previous[field]
            meta["status"] = previous.get("status", "new")
        for field in self.spec["fields"]:
            if field in fields and fields[field] is not None:
                meta[field] = clean(fields[field])[:1000] if field != "note" else no_dashes(str(fields[field]))[:4000]
        meta["title"] = clean(fields.get("title") or meta.get("title") or key_value)[:200]
        meta.setdefault(key_name, key_value)
        body = str(meta.pop("note", "") or "")
        if previous:
            unchanged = all(previous.get(f) == meta.get(f) for f in self.spec["fields"] if f != "note") and previous.get("body", "") == body.strip()
            if unchanged:
                return {**previous}, "unchanged"
        _atomic_write(path, render(meta, body))
        return {**meta, "note": body, "body": body}, "created" if previous is None else "updated"

    def set_record(self, record_id: str, fields: dict) -> dict:
        current = self.record(record_id)
        if current is None:
            raise ValueError(f"No record {record_id} in {self.id}.")
        merged = {**{f: current.get(f) for f in self.spec["fields"] if f in current}, "note": current.get("body", ""), **(fields or {})}
        merged[self.spec["key"]] = current.get(self.spec["key"], record_id)
        record, _ = self.write_record(merged)
        return record

    def set_record_status(self, record_id: str, status: str) -> dict:
        return self.set_record(record_id, {"status": clean(status)[:40]})

    def delete_record(self, record_id: str) -> bool:
        path = self.records_dir / f"{slug(record_id)}.md"
        if path.is_file():
            _inside(self.domain, str(path.relative_to(self.domain))).unlink()
            return True
        return False

    # prompts -------------------------------------------------------------------
    def records_context(self) -> str:
        items = self.records()
        head = [f"Existing records ({len(items)}), newest first: key | title | status | updated"]
        for item in items[:MAX_RECORDS_IN_CONTEXT]:
            head.append(f"- {item.get(self.spec['key'], item['id'])} | {item.get('title', '')} | {item.get('status', '')} | {str(item.get('updated', ''))[:10]}")
        if len(items) > MAX_RECORDS_IN_CONTEXT:
            head.append(f"- ... and {len(items) - MAX_RECORDS_IN_CONTEXT} more (use the records tool to search)")
        return "\n".join(head)

    def system_prompt(self, mode: str = "run") -> str:
        parts = [f"# {self.spec['name']} (agent app `{self.id}`)", self.prompt, RULES,
                 f"Record fields: {', '.join(self.spec['fields'])}. Key field: `{self.spec['key']}`.",
                 f"Web search: {'available (web_search; fetch_url for a page)' if self.spec['web'] else 'off for this app'}.",
                 self.connectors_line(),
                 f"Today is {date.today():%A %Y-%m-%d}."]
        memory = memory_digest(self.domain)
        if memory:
            parts.append("## What the OS knows about " + core.owner_name() + " and the work\n" + memory)
        parts.append("## " + self.records_context())
        if mode == "chat":
            parts.append("You are in a conversation with " + core.owner_name() + " about this app and its records. Answer directly; "
                         "use write_record only when he asks for a change or clearly wants one kept.")
        return "\n\n".join(p for p in parts if p)

    def connectors_line(self) -> str:
        allowed = self.spec.get("connectors") or []
        if not allowed:
            return "Connectors: none. You can read the repo and the web, and write this app's records."
        names = "all connected services" if allowed == "all" else ", ".join(allowed)
        auto = sorted(k for k, v in self.spec.get("policy", {}).items() if v == "auto" and k != "*")
        return (f"Connectors: {names}. Tools are named <connector>.<tool>. "
                + (f"These act without asking: {', '.join(auto)}. " if auto else "")
                + "Every other action (send, create, post) is queued for the owner's approval; say so in your report.")

    # routines ------------------------------------------------------------------
    def schedules(self) -> list[dict]:
        """Every schedule trigger, the top-level every/at first (old app files keep working)."""
        out = []
        if self.spec.get("every"):
            out.append({"every": self.spec["every"]})
        if self.spec.get("at"):
            out.append({"at": self.spec["at"]})
        out += [{k: v for k, v in t.items() if k in ("every", "at")} for t in self.spec.get("triggers", []) if t["type"] == "schedule"]
        return out

    def routines(self) -> list[dict]:
        items = []
        for i, when in enumerate(self.schedules()):
            items.append({"id": "run" if i == 0 else f"run-{i}", "name": f"{self.spec['name']} run", **when})
        if not items:
            items.append({"id": "run", "name": f"{self.spec['name']} run"})
        email = [t for t in self.spec.get("triggers", []) if t["type"] == "email"]
        for i, trig in enumerate(email):
            items.append({"id": f"email-{i}", "name": f"{self.spec['name']} email check", "every": trig["every"]})
        return items

    def commands(self) -> list[str]:
        return [t["command"] for t in self.spec.get("triggers", []) if t["type"] == "telegram"]

    def connectors(self) -> list[str] | str:
        return self.spec.get("connectors", [])

    # state.json: what triggers have already seen (email uids)
    @property
    def state_path(self) -> Path:
        return _inside(self.domain, f"{APPS}/{self.id}/state.json")

    def state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, data: dict) -> None:
        _atomic_write(self.state_path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    def last_run(self) -> dict | None:
        root = self.domain / RUNS
        if not root.is_dir():
            return None
        agent = f"app:{self.id}"
        for day in sorted(root.iterdir(), reverse=True)[:30]:
            for path in sorted(day.glob("*.json"), reverse=True):
                try:
                    run = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if run.get("agent") == agent:
                    return {"id": run.get("id"), "started": run.get("started"), "finished": run.get("finished"),
                            "answer": str(run.get("answer", ""))[:600], "usage": run.get("usage", {}),
                            "changes": run.get("changes", {}), "error": run.get("error")}
        return None

    def usage(self) -> dict:
        root = self.domain / RUNS
        out = {"today": 0, "7d": 0, "30d": 0, "runs_30d": 0, "by_agent": {}, "app": self.id}
        if not root.is_dir():
            return out
        today = date.today()
        agent = f"app:{self.id}"
        for day in root.iterdir():
            try:
                age = (today - date.fromisoformat(day.name)).days
            except ValueError:
                continue
            if age > 30:
                continue
            for path in day.glob("*.json"):
                try:
                    run = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if run.get("agent") != agent:
                    continue
                usage = run.get("usage") or {}
                tokens = int(usage.get("input", 0) or 0) + int(usage.get("output", 0) or 0)
                out["30d"] += tokens
                out["runs_30d"] += 1
                if age <= 7:
                    out["7d"] += tokens
                if age == 0:
                    out["today"] += tokens
        out["by_agent"] = {agent: out["30d"]}
        return out


# ---------- tools the app exposes to its own model ----------

class RecordTools:
    """`records` and `write_record`, scoped to one app. Composes with the OS base tools."""

    def __init__(self, app: AgentApp, allow_write: bool = True):
        self.app = app
        self.allow_write = allow_write
        self.calls: list[dict] = []

    def specs(self) -> list[dict]:
        fields = self.app.spec["fields"]
        specs = [{"name": "records", "description": f"Search this app's records ({self.app.id}). Returns matching records with every field.",
                  "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "substring to match in any field; empty for all"},
                                                                      "status": {"type": "string"}, "limit": {"type": "integer"}}}}]
        if self.allow_write:
            specs.append({"name": "write_record",
                          "description": f"Add or update one record of this app. The key field `{self.app.spec['key']}` identifies it: same key updates. Fields: {', '.join(fields)}.",
                          "input_schema": {"type": "object", "properties": {f: {"type": "string"} for f in fields}, "required": [self.app.spec["key"]]}})
        return specs

    def call(self, name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        if name == "records":
            query = clean(args.get("query") or "").lower()
            status = clean(args.get("status") or "")
            limit = max(1, min(int(args.get("limit") or 40), 200))
            out = []
            for record in self.app.records():
                if status and record.get("status") != status:
                    continue
                blob = " ".join(str(v) for v in record.values()).lower()
                if query and query not in blob:
                    continue
                out.append({k: v for k, v in record.items() if k not in {"source", "app"}})
                if len(out) >= limit:
                    break
            self.calls.append({"name": name, "args": args, "count": len(out)})
            return json.dumps(out, ensure_ascii=False)
        if name == "write_record" and self.allow_write:
            if isinstance(args.get("fields"), dict):  # tolerate {id, fields: {...}} as well as flat fields
                nested = dict(args["fields"])
                if args.get("id") and not nested.get(self.app.spec["key"]):
                    nested[self.app.spec["key"]] = args["id"]
                args = nested
            record, outcome = self.app.write_record(args)
            self.calls.append({"name": name, "args": args, "outcome": outcome, "id": record.get("id")})
            return json.dumps({"ok": True, "outcome": outcome, "id": record.get("id")})
        raise ValueError(f"Unknown tool {name}")


class AppOpsTools:
    """Create, update, run and delete agent apps. Given to the OS Operator so an app can be made
    by asking for it."""

    def __init__(self, domain: Path, runtime=None):
        self.domain = Path(domain)
        self.runtime = runtime
        self.calls: list[dict] = []

    def specs(self) -> list[dict]:
        spec_props = {"id": {"type": "string", "description": "slug, e.g. art-shows"}, "name": {"type": "string"},
                      "description": {"type": "string"}, "icon": {"type": "string", "description": "one character"},
                      "every": {"type": "string", "description": "15m, 6h, 1d; or use at"}, "at": {"type": "string", "description": "HH:MM daily"},
                      "fields": {"type": "array", "items": {"type": "string"}, "description": "record fields, lowercase; status and note are added"},
                      "key": {"type": "string", "description": "the field that identifies a record, usually url"},
                      "web": {"type": "boolean"}, "notify": {"type": "boolean"},
                      "model": {"type": "string", "description": "default, deep, or provider:model (openai:gpt-5, gemini:gemini-2.5-pro, openrouter:<id>, ollama:<name>)"},
                      "triggers": {"type": "array", "description": "schedule {type, every|at}, email {type, query (Gmail search), every}, telegram {type, command like /scout}",
                                   "items": {"type": "object"}},
                      "connectors": {"description": "connector ids the bot may use: gmail, calendar, telegram, instagram, site, mcp.<server>; or \"all\"",
                                     "type": "array", "items": {"type": "string"}},
                      "policy": {"type": "object", "description": "per tool: auto (just do it) or ask (queue for the owner). e.g. {\"calendar.create_event\": \"auto\", \"*\": \"ask\"}. Print, Photos and canon always ask."}}
        return [
            {"name": "list_apps", "description": "List the agent apps that exist, with cadence and record counts.", "input_schema": {"type": "object", "properties": {}}},
            {"name": "create_app", "description": "Create an agent app from a spec and a standing prompt. It appears in the sidebar at once and runs on its cadence.",
             "input_schema": {"type": "object", "properties": {**spec_props, "prompt": {"type": "string", "description": "the standing instructions, written for the model that will run it"}},
                              "required": ["id", "name", "fields", "prompt"]}},
            {"name": "update_app", "description": "Change an agent app's spec fields and/or prompt.",
             "input_schema": {"type": "object", "properties": {**spec_props, "prompt": {"type": "string"}}, "required": ["id"]}},
            {"name": "run_app", "description": "Run an agent app's routine now and return what it changed.",
             "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
            {"name": "delete_app", "description": "Retire an agent app (moved to hq/os/apps/_deleted, records kept).",
             "input_schema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}},
        ]

    def call(self, name: str, args: dict) -> str:
        args = dict(args or {})
        self.calls.append({"name": name, "args": args})
        if name == "list_apps":
            return json.dumps([{"id": a.id, "name": a.spec["name"], "every": a.spec["every"], "at": a.spec["at"],
                                "records": len(a.records())} for a in load(self.domain).values()], ensure_ascii=False)
        if name == "create_app":
            prompt = args.pop("prompt", "")
            app = create(self.domain, args, prompt)
            _after_change(self.runtime)
            return json.dumps({"ok": True, "id": app.id, "message": f"{app.spec['name']} created; it shows under APPS and runs {app.spec['every'] or ('daily at ' + str(app.spec['at'])) or 'on demand'}."})
        if name == "update_app":
            prompt = args.pop("prompt", None)
            app = update(self.domain, args.pop("id"), args or None, prompt)
            _after_change(self.runtime)
            return json.dumps({"ok": True, "id": app.id})
        if name == "run_app":
            return json.dumps(run(self.domain, args.get("id"), runtime=self.runtime), ensure_ascii=False, default=str)[:4000]
        if name == "delete_app":
            delete(self.domain, args.get("id"))
            _after_change(self.runtime)
            return json.dumps({"ok": True, "id": args.get("id"), "message": "retired; records kept"})
        raise ValueError(f"Unknown tool {name}")


class Composite:
    """Several toolbelts as one; the first belt that declares a name owns it."""

    def __init__(self, belts: list):
        self.belts = [b for b in belts if b is not None]

    def specs(self) -> list[dict]:
        seen, out = set(), []
        for belt in self.belts:
            try:
                items = belt.specs() or []
            except Exception:
                items = []
            for spec in items:
                if spec.get("name") not in seen:
                    seen.add(spec["name"])
                    out.append(spec)
        return out

    def call(self, name: str, args: dict) -> str:
        for belt in self.belts:
            try:
                names = {s.get("name") for s in belt.specs() or []}
            except Exception:
                names = set()
            if name in names:
                return belt.call(name, args)
        raise ValueError(f"Unknown tool {name}")


def _after_change(runtime) -> None:
    invalidate()
    if runtime is not None:
        try:
            runtime.reconcile()
        except Exception:
            pass


# ---------- dependencies (lazy, injectable) ----------

def _deps(deps: dict | None) -> dict:
    out = dict(deps or {})
    for name in ("llm", "agentrun", "chats", "basetools", "connectors"):
        if name in out:
            continue
        try:
            out[name] = __import__(f"aihq.{name}", fromlist=[name])
        except Exception:
            out[name] = None
    return out


def _pick_model(deps: dict, kind: str, web: bool, override=None):
    if override is not None:
        try:
            setattr(override, "web", web)
        except Exception:
            pass
        return override
    llm = deps.get("llm")
    if llm is None:
        raise ValueError("No model layer (aihq.llm) is available yet.")
    if ":" in str(kind) and hasattr(llm, "resolve"):
        model = llm.resolve(kind)
    else:
        pick = getattr(llm, "pick")
        try:
            model = pick(kind, web=web)
        except TypeError:
            model = pick(kind)
    web = web and bool(getattr(model, "supports_web", True))
    try:
        setattr(model, "web", web)
    except Exception:
        pass
    return model


def app_ops(domain: Path, runtime=None) -> dict:
    """The agent-app operations as plain callables, for basetools.BaseToolbelt(app_ops=...)."""
    domain = Path(domain)

    def create_app(prompt: str = "", **spec):
        app = create(domain, spec, prompt)
        _after_change(runtime)
        return {"ok": True, "id": app.id, "message": f"{app.spec['name']} created; it shows under APPS and runs {app.spec['every'] or app.spec['at'] or 'on demand'}."}

    def update_app(id: str, changes: dict | None = None, prompt: str | None = None, **rest):
        changes = dict(changes or {})
        changes.update(rest)
        if prompt is None:
            prompt = changes.pop("prompt", None)
        app = update(domain, id, changes or None, prompt)
        _after_change(runtime)
        return {"ok": True, "id": app.id, "spec": app.spec}

    def delete_app(id: str):
        delete(domain, id)
        _after_change(runtime)
        return {"ok": True, "id": id, "message": "retired; records kept"}

    def run_app(id: str):
        return run(domain, id, runtime=runtime)

    return {"create_app": create_app, "update_app": update_app, "delete_app": delete_app, "run_app": run_app}


def _base_toolbelt(deps: dict, domain: Path, app_id: str | None, runtime=None, with_app_ops: bool = False):
    base = deps.get("basetools")
    if base is None or not hasattr(base, "BaseToolbelt"):
        return None
    cls = base.BaseToolbelt
    ops = app_ops(domain, runtime) if with_app_ops else None
    for attempt in ({"app_id": app_id, "app_ops": ops, "agent": f"app:{app_id}" if app_id else "operator"},
                    {"app_id": app_id, "app_ops": ops}, {"app_id": app_id}, {}):
        try:
            return cls(domain, **attempt)
        except TypeError:
            continue
    return None


def _connector_toolbelt(domain: Path, app: "AgentApp", runtime=None, connectors_module=None):
    """The connectors this app may use, with its policy. None when there are none or the
    connector layer is not installed yet."""
    allowed = app.spec.get("connectors") or []
    if not allowed:
        return None
    module = connectors_module
    if module is None:
        try:
            from . import connectors as module  # type: ignore
        except Exception:
            return None
    cls = getattr(module, "ConnectorTools", None)
    if cls is None:
        return None
    try:
        return cls(domain, allowed, app.spec.get("policy") or {"*": "ask"}, f"app:{app.id}", runtime)
    except TypeError:
        try:
            return cls(domain, allowed, app.spec.get("policy") or {"*": "ask"}, f"app:{app.id}")
        except Exception:
            return None
    except Exception:
        return None


def _belt(deps: dict, domain: Path, app: "AgentApp", runtime=None, with_app_ops: bool = False):
    belts = [RecordTools(app), _connector_toolbelt(domain, app, runtime, deps.get("connectors")),
             _base_toolbelt(deps, domain, app.id, runtime, with_app_ops)]
    return Composite([b for b in belts if b is not None])


def _loop(deps: dict, model, system: str, messages: list[dict], toolbelt, max_steps: int = 16) -> dict:
    agentrun = deps.get("agentrun")
    if agentrun is None:
        raise ValueError("No agent loop (aihq.agentrun) is available yet.")
    try:
        result = agentrun.run(model, system, messages, toolbelt, max_steps=max_steps, web=bool(getattr(model, "web", False)))
    except TypeError:
        result = agentrun.run(model, system, messages, toolbelt, max_steps=max_steps)
    if not isinstance(result, dict):
        result = {"answer": str(result)}
    result.setdefault("answer", "")
    result.setdefault("tool_calls", [])
    result.setdefault("usage", {})
    return result


class _Chats:
    """Normalizes aihq.chats whether it exposes module functions (domain first) or a Chats class."""

    def __init__(self, module, domain: Path):
        self.domain = Path(domain)
        self.impl = None
        self.module = module
        if module is None:
            return
        if hasattr(module, "Chats"):
            try:
                self.impl = module.Chats(self.domain)
            except TypeError:
                self.impl = module.Chats()

    def _call(self, name: str, *args, **kwargs):
        if self.impl is not None and hasattr(self.impl, name):
            return getattr(self.impl, name)(*args, **kwargs)
        if self.module is None or not hasattr(self.module, name):
            raise ValueError("Conversations (aihq.chats) are not available yet.")
        function = getattr(self.module, name)
        try:
            params = list(inspect.signature(function).parameters)
        except (TypeError, ValueError):
            params = []
        if params and params[0] in {"domain", "root", "path"}:
            return function(self.domain, *args, **kwargs)
        return function(*args, **kwargs)

    def create(self, title, agent, app=None):
        return self._call("create", title, agent, app=app)

    def append(self, thread_id, role, text, images=None, tool_calls=None, usage=None):
        return self._call("append", thread_id, role, text, images=images or [], tool_calls=tool_calls or [], usage=usage or {})

    def history(self, thread_id, limit_chars=24000):
        return self._call("history", thread_id, limit_chars)

    def get(self, thread_id):
        return self._call("get", thread_id)

    def list(self, app=None):
        return self._call("list", app=app)

    def needs_summary(self, thread_id):
        try:
            return bool(self._call("needs_summary", thread_id))
        except ValueError:
            return False

    def set_summary(self, thread_id, text):
        return self._call("set_summary", thread_id, text)


def _thread_id(thread) -> str:
    if isinstance(thread, dict):
        return str(thread.get("id"))
    return str(thread)


# ---------- the catalog of agent apps ----------

def _apps_root(domain: Path) -> Path:
    return Path(domain) / APPS


def _stamp(domain: Path) -> float:
    root = _apps_root(domain)
    if not root.is_dir():
        return -1.0
    latest = root.stat().st_mtime
    for folder in root.iterdir():
        if folder.name == DELETED or not folder.is_dir():
            continue
        for name in ("app.json", "prompt.md"):
            path = folder / name
            if path.is_file():
                latest = max(latest, path.stat().st_mtime)
        latest = max(latest, folder.stat().st_mtime)
    return latest


def load(domain: Path, errors: dict | None = None) -> dict[str, AgentApp]:
    """{id: AgentApp} from hq/os/apps/*/app.json, cached until any app file changes."""
    domain = Path(domain).resolve()
    stamp = _stamp(domain)
    key = str(domain)
    with _lock:
        cached = _cache.get(key)
    if cached and cached[0] == stamp and errors is None:
        return cached[1]
    apps: dict[str, AgentApp] = {}
    root = _apps_root(domain)
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if folder.name == DELETED or not folder.is_dir() or not (folder / "app.json").is_file():
                continue
            try:
                spec = json.loads((folder / "app.json").read_text(encoding="utf-8"))
                prompt = (folder / "prompt.md").read_text(encoding="utf-8") if (folder / "prompt.md").is_file() else ""
                app = AgentApp(domain, spec, prompt)
                if app.id != folder.name:
                    raise ValueError(f"folder {folder.name} holds app id {app.id}")
                apps[app.id] = app
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                if errors is not None:
                    errors[folder.name] = str(exc)[:300]
    with _lock:
        _cache[key] = (stamp, apps)
    return apps


def invalidate() -> None:
    with _lock:
        _cache.clear()


def get(domain: Path, app_id: str) -> AgentApp:
    app = load(domain).get(str(app_id))
    if app is None:
        raise ValueError(f"No agent app `{app_id}`.")
    return app


def create(domain: Path, spec: dict, prompt: str) -> AgentApp:
    normalized = validate(spec)
    folder = _inside(domain, f"{APPS}/{normalized['id']}")
    if folder.exists():
        raise ValueError(f"An app named {normalized['id']} already exists; use update_app.")
    prompt = no_dashes(prompt or "").strip()
    if len(prompt) < 20:
        raise ValueError("The prompt is the app. Write at least a few sentences of standing instructions.")
    _atomic_write(folder / "app.json", json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(folder / "prompt.md", prompt + "\n")
    invalidate()
    log_line(domain, "os", f"agent app created: {normalized['id']} ({normalized['name']})", f"{APPS}/{normalized['id']}")
    return get(domain, normalized["id"])


def update(domain: Path, app_id: str, spec_changes: dict | None = None, prompt: str | None = None) -> AgentApp:
    app = get(domain, app_id)
    merged = {**app.spec, **{k: v for k, v in (spec_changes or {}).items() if k not in {"id", "kind", "schema_version"}}}
    if spec_changes and "every" in spec_changes and spec_changes["every"]:
        merged["at"] = None
    if spec_changes and "at" in spec_changes and spec_changes["at"]:
        merged["every"] = None
    normalized = validate(merged)
    folder = _inside(domain, f"{APPS}/{app.id}")
    _atomic_write(folder / "app.json", json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    if prompt is not None:
        text = no_dashes(prompt).strip()
        if len(text) < 20:
            raise ValueError("The prompt is the app. Keep at least a few sentences.")
        _atomic_write(folder / "prompt.md", text + "\n")
    invalidate()
    log_line(domain, "os", f"agent app updated: {app.id}", f"{APPS}/{app.id}")
    return get(domain, app.id)


def delete(domain: Path, app_id: str) -> Path:
    app = get(domain, app_id)
    source = _inside(domain, f"{APPS}/{app.id}")
    target = _inside(domain, f"{APPS}/{DELETED}/{app.id}-{datetime.now():%Y%m%d-%H%M%S}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    invalidate()
    log_line(domain, "os", f"agent app retired: {app.id}", str(target.relative_to(domain.resolve())))
    return target


def records(domain: Path, app_id: str) -> list[dict]:
    return get(domain, app_id).records()


def set_record_status(domain: Path, app_id: str, record_id: str, status: str) -> dict:
    return get(domain, app_id).set_record_status(record_id, status)


# ---------- running and chatting ----------

def _snapshot_records(app: AgentApp) -> dict:
    return {r["id"]: {k: v for k, v in r.items() if k not in {"updated"}} for r in app.records()}


def _diff(before: dict, after: dict) -> dict:
    new = [after[k] for k in after if k not in before]
    changed = [after[k] for k in after if k in before and before[k] != after[k]]
    return {"new": new, "updated": changed, "counts": {"new": len(new), "updated": len(changed), "total": len(after)}}


def run(domain: Path, app_id: str, model=None, runtime=None, deps: dict | None = None,
        prompt: str | None = None, trigger: str = "schedule") -> dict:
    """One routine run of an agent app: the loop, the diff, the note, the run file, the log line.
    `prompt` replaces the standing "run your routine" (an email trigger passes the new mail)."""
    domain = Path(domain).resolve()
    app = get(domain, app_id)
    deps = _deps(deps)
    started = datetime.now()
    run_id = f"{started:%H%M%S}-{uuid.uuid4().hex[:8]}"
    before = _snapshot_records(app)
    belt = _belt(deps, domain, app, runtime)
    result: dict = {}
    error = None
    try:
        model = _pick_model(deps, app.spec["model"], app.spec["web"], model)
        result = _loop(deps, model, app.system_prompt("run"), [{"role": "user", "content": prompt or RUN_PROMPT}], belt)
    except Exception as exc:  # the run is recorded either way; the failure is in the file and the log
        error = str(exc)[:600]
    if not error and result.get("stop") == "error":  # the loop ended on an API or tool error after step one
        error = (result.get("answer") or "the run stopped on an error")[:600]
    after = _snapshot_records(app)
    changes = _diff(before, after)
    finished = datetime.now()
    usage = result.get("usage") or {}
    run_record = {"id": run_id, "agent": f"app:{app.id}", "app": app.id, "prompt": prompt or RUN_PROMPT, "trigger": trigger,
                  "answer": result.get("answer", "") if not error else "", "error": error,
                  "tool_calls": result.get("tool_calls", []), "usage": {"input": int(usage.get("input", usage.get("input_tokens", 0)) or 0),
                                                                        "output": int(usage.get("output", usage.get("output_tokens", 0)) or 0)},
                  "started": started.isoformat(timespec="seconds"), "finished": finished.isoformat(timespec="seconds"),
                  "seconds": round((finished - started).total_seconds(), 1), "changes": changes,
                  "model": getattr(model, "name", None) if model is not None else None}
    path = save_run(domain, run_record)
    note = None
    counts = changes["counts"]
    if app.spec["notify"] and (counts["new"] or counts["updated"]) and not error:
        lines = [f"- {r.get('title') or r['id']}" + (f" ({r.get(app.spec['key'])})" if r.get(app.spec["key"]) and app.spec["key"] != "title" else "")
                 for r in (changes["new"] + changes["updated"])[:15]]
        summary = (result.get("answer") or "").strip()
        body = "\n".join(lines) + ("\n\n" + summary[:1500] if summary else "") + f"\n\nRun: {path.relative_to(domain)}"
        note = add_inbox(domain, "note", f"{app.spec['name']}: {counts['new']} new, {counts['updated']} updated", body,
                         agent=f"app:{app.id}", app=app.id, run=run_id)
    if error:  # a failed run is never silent: it waits in the Inbox until the owner looks
        note = add_inbox(domain, "note", f"{app.spec['name']}: run failed",
                         f"{error}\n\nCheck the model backend with `./hq doctor` (API key, model, web search on your account), "
                         f"then press Run now on the app's board.\n\nRun: {path.relative_to(domain)}",
                         agent=f"app:{app.id}", app=app.id, run=run_id)
    _telegram_notify(domain, app, result, counts, error, deps)
    what = (f"run failed: {error[:120]}" if error else
            f"run: {counts['new']} new, {counts['updated']} updated of {counts['total']} ({run_record['usage']['input']}+{run_record['usage']['output']} tokens)")
    log_line(domain, f"app:{app.id}", what, str(path.relative_to(domain)))
    return {"app": app.id, "run": run_id, "answer": run_record["answer"], "error": error, "changes": counts,
            "note": note["id"] if note else None, "usage": run_record["usage"], "path": str(path.relative_to(domain))}


def chat(domain: Path, app_id: str, thread_id: str | None, text: str, images: list | None = None,
         model=None, runtime=None, deps: dict | None = None) -> dict:
    """Talk to an agent app inside a persistent thread."""
    domain = Path(domain).resolve()
    app = get(domain, app_id)
    deps = _deps(deps)
    text = no_dashes(text or "").strip()
    if not text and not images:
        raise ValueError("Say something, or attach a photo.")
    threads = _Chats(deps.get("chats"), domain)
    if not thread_id:
        thread = threads.create(text[:60] or f"{app.spec['name']} chat", f"app:{app.id}", app=app.id)
        thread_id = _thread_id(thread)
    threads.append(thread_id, "user", text, images=images or [])
    history = threads.history(thread_id)
    if not isinstance(history, list) or not history:
        history = [{"role": "user", "content": text}]
    belt = _belt(deps, domain, app, runtime)
    started = datetime.now()
    model = _pick_model(deps, app.spec["model"], app.spec["web"], model)
    result = _loop(deps, model, app.system_prompt("chat"), history, belt)
    answer = result.get("answer", "")
    usage = result.get("usage") or {}
    threads.append(thread_id, "assistant", answer, tool_calls=result.get("tool_calls", []), usage=usage)
    run_id = f"{started:%H%M%S}-{uuid.uuid4().hex[:8]}"
    path = save_run(domain, {"id": run_id, "agent": f"app:{app.id}", "app": app.id, "thread": thread_id, "prompt": text,
                             "answer": answer, "tool_calls": result.get("tool_calls", []),
                             "usage": {"input": int(usage.get("input", usage.get("input_tokens", 0)) or 0),
                                       "output": int(usage.get("output", usage.get("output_tokens", 0)) or 0)},
                             "started": started.isoformat(timespec="seconds"), "finished": datetime.now().isoformat(timespec="seconds")})
    if threads.needs_summary(thread_id):
        try:
            summary = _loop(deps, model, "Summarize this conversation in three plain lines for someone resuming it later. No em dashes.",
                            history + [{"role": "assistant", "content": answer}, {"role": "user", "content": "Summarize."}],
                            Composite([]), max_steps=1)
            threads.set_summary(thread_id, summary.get("answer", ""))
        except Exception:
            pass
    log_line(domain, f"app:{app.id}", f"chat: {text[:80]}", str(path.relative_to(domain)))
    return {"thread": thread_id, "answer": answer, "run": run_id, "tool_calls": result.get("tool_calls", []), "usage": usage}


def _telegram_notify(domain: Path, app: "AgentApp", result: dict, counts: dict, error: str | None, deps: dict) -> None:
    """A bot that may use Telegram tells the owner what happened, unless it already messaged them
    itself this run (Show Scout sends its own one message)."""
    allowed = app.spec.get("connectors") or []
    if not app.spec.get("notify") or not (allowed == "all" or "telegram" in allowed):
        return
    if any(str(call.get("name", "")).startswith("telegram.") for call in result.get("tool_calls", []) or []):
        return
    if not error and not (counts.get("new") or counts.get("updated")):
        return
    text = (f"{app.spec['name']}: the run failed. {error[:300]}" if error else
            f"{app.spec['name']}: {counts.get('new', 0)} new, {counts.get('updated', 0)} updated.\n\n"
            + (result.get("answer") or "").strip()[:1500])
    gateway = deps.get("telegram_gateway")
    try:
        if gateway is None:
            from . import telegram_gateway as gateway  # type: ignore
        gateway.notify(text, domain=domain)
    except Exception:
        pass  # no token, no network: the Inbox note is the record


def check_email(domain: Path, app_id: str, index: int = 0, runtime=None, deps: dict | None = None, model=None) -> dict:
    """An email trigger: search Gmail with the trigger's query (a read), run the bot only on mail
    it has not seen. Quiet when there is nothing new: no run file, just the state."""
    domain = Path(domain).resolve()
    app = get(domain, app_id)
    deps = _deps(deps)
    triggers = [t for t in app.spec.get("triggers", []) if t["type"] == "email"]
    if index >= len(triggers):
        raise ValueError(f"{app.spec['name']} has no email trigger {index}.")
    query = triggers[index]["query"]
    belt = _connector_toolbelt(domain, app, runtime, deps.get("connectors"))
    if belt is None:
        module = deps.get("connectors")
        cls = getattr(module, "ConnectorTools", None) if module else None
        if cls is None:
            try:
                from . import connectors as module  # type: ignore
                cls = module.ConnectorTools
            except Exception:
                return {"app": app.id, "skipped": "the Gmail connector is not installed yet"}
        belt = cls(domain, ["gmail"], {"*": "ask"}, f"app:{app.id}", runtime)
    raw = belt.call("gmail.search", {"query": query, "limit": 20})
    try:
        found = json.loads(raw)
    except (TypeError, ValueError):
        return {"app": app.id, "error": f"gmail.search did not return JSON: {str(raw)[:200]}"}
    messages = found.get("messages", found) if isinstance(found, dict) else found
    messages = [m for m in messages or [] if isinstance(m, dict)]
    state = app.state()
    seen = set(state.get("email_seen", {}).get(str(index), []))
    new = [m for m in messages if str(m.get("uid")) not in seen]
    state.setdefault("email_seen", {})[str(index)] = sorted(seen | {str(m.get("uid")) for m in messages})[-500:]
    state["email_checked"] = datetime.now().isoformat(timespec="seconds")
    app.save_state(state)
    if not new:
        return {"app": app.id, "new_messages": 0}
    lines = [f"- uid {m.get('uid')} · {m.get('date', '')} · from {m.get('from', '')} · {m.get('subject', '')}\n  {str(m.get('snippet', ''))[:300]}"
             for m in new[:20]]
    prompt = (f"New messages matching `{query}` ({len(new)}):\n" + "\n".join(lines)
              + "\n\nHandle them per your instructions. Use gmail.read for a full message. Report what you did.")
    result = run(domain, app.id, model=model, runtime=runtime, deps=deps, prompt=prompt, trigger="email")
    return {**result, "new_messages": len(new)}


def commands(domain: Path) -> dict[str, str]:
    """Telegram commands the bots answer to: {"/scout": "art-shows"}."""
    out = {}
    for app_id, app in load(domain).items():
        for command in app.commands():
            out.setdefault(command, app_id)
    return out


# ---------- how the OS sees an agent app (the module App contract) ----------

class AgentAppAdapter:
    kind = "agent"

    def __init__(self, app: AgentApp, runtime=None):
        self.app = app
        self.id = app.id
        self.runtime = runtime

    def manifest(self) -> dict:
        s = self.app.spec
        return {"id": s["id"], "name": s["name"], "icon": s["icon"], "kind": "agent", "description": s["description"],
                "views": [{"id": "board", "label": "Board", "count": "records"}, {"id": "chat", "label": "Chat"},
                          {"id": "settings", "label": "Settings"}],
                "web": {"script": "/agent-app.js", "style": "/agent-app.css"}, "every": s["every"], "at": s["at"]}

    def snapshot(self) -> dict:
        items = self.app.records()
        today = f"{date.today():%Y-%m-%d}"
        return {"records": items, "fields": self.app.spec["fields"], "key": self.app.spec["key"],
                "counts": {"records": len(items), "new_today": sum(1 for r in items if str(r.get("created", "")).startswith(today)),
                           "status_new": sum(1 for r in items if r.get("status") == "new")},
                "prompt": self.app.prompt, "spec": self.app.spec, "last_run": self.app.last_run(),
                "commands": self.app.commands(), "state": {k: v for k, v in self.app.state().items() if k != "email_seen"},
                "actions": ["record.set", "record.delete", "app.update", "app.run", "app.delete"]}

    def action(self, payload: dict) -> dict:
        verb = (payload or {}).get("action")
        domain = self.app.domain
        if verb == "record.set":
            return {"ok": True, "record": self.app.set_record(str(payload.get("id")), payload.get("fields") or {})}
        if verb == "record.delete":
            return {"ok": self.app.delete_record(str(payload.get("id")))}
        if verb == "app.update":
            app = update(domain, self.id, payload.get("spec"), payload.get("prompt"))
            _after_change(self.runtime)
            return {"ok": True, "spec": app.spec}
        if verb == "app.run":
            return run(domain, self.id, runtime=self.runtime)
        if verb == "app.delete":
            target = delete(domain, self.id)
            _after_change(self.runtime)
            return {"ok": True, "moved_to": str(target.relative_to(domain))}
        raise ValueError(f"Unknown action for agent app {self.id}: {verb}")

    def ask(self, text: str, agent=None) -> dict:
        return chat(self.app.domain, self.id, None, text, runtime=self.runtime)

    def chat(self, thread_id, text, agent=None, images=None) -> dict:
        return chat(self.app.domain, self.id, thread_id, text, images=images, runtime=self.runtime)

    def routines(self) -> list[dict]:
        return self.app.routines()

    def run_routine(self, routine_id: str) -> dict:
        if routine_id == "run" or str(routine_id).startswith("run-"):
            return run(self.app.domain, self.id, runtime=self.runtime)
        if str(routine_id).startswith("email-") and routine_id[6:].isdigit():
            return check_email(self.app.domain, self.id, int(routine_id[6:]), runtime=self.runtime)
        raise ValueError(f"{self.app.spec['name']} has no routine `{routine_id}`.")

    def agents(self) -> list[dict]:
        last = self.app.last_run()
        return [{"id": "agent", "name": self.app.spec["name"], "role": "agent app", "description": self.app.spec["description"],
                 "last_run": last.get("finished") if last else None, "tokens_today": self.app.usage()["today"],
                 "last_error": last.get("error") if last else None}]

    def usage(self) -> dict:
        return self.app.usage()

    def doctor(self) -> list[dict]:
        last = self.app.last_run()
        return [{"status": "ok", "name": "spec", "detail": f"{self.app.spec['name']}, {self.app.spec['every'] or self.app.spec['at'] or 'on demand'}, {len(self.app.records())} records"},
                {"status": "FAIL" if last and last.get("error") else "info", "name": "last run",
                 "detail": (last.get("error") or last.get("finished") or "") if last else "never"}]

    def cli(self, argv: list[str], out=None) -> int:
        import sys as _sys
        out = out or _sys.stdout
        if not argv or argv[0] in {"records", "board"}:
            for r in self.app.records():
                print(f"{r.get('status', ''):>8}  {r.get('title', '')}  {r.get(self.app.spec['key'], '')}", file=out)
            return 0
        if argv[0] == "run":
            print(json.dumps(run(self.app.domain, self.id, runtime=self.runtime), indent=2, default=str), file=out)
            return 0
        if argv[0] == "chat":
            result = chat(self.app.domain, self.id, None, " ".join(argv[1:]), runtime=self.runtime)
            print(result["answer"], file=out)
            return 0
        print(f"./hq app {self.id} records | run | chat <text>", file=out)
        return 2


def adapters(domain: Path, runtime=None) -> dict:
    return {app_id: AgentAppAdapter(app, runtime) for app_id, app in load(domain).items()}


def manifests(domain: Path, runtime=None) -> dict:
    return {app_id: adapter.manifest() for app_id, adapter in adapters(domain, runtime).items()}
