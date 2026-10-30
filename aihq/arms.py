"""ARMS views for the headquarters: Today, Recos, Skills, Routines, Memory, Usage, Apps.

Everything here is read from files that already exist: the vault (`hq/HQ.md` when the
checkout keeps one), skill files, CLAUDE.md and MEMORY.md files, and Claude Code's own session
logs in `~/.claude/projects`. Nothing is invented: a number this module cannot measure is shown
as "not measured", and money is shown only when you set token rates in `system/arms.json`.
Checkout-specific reco cards come from `extra_recos` in curator.json.

The only writes are your own local state (cart, parked notes) in `.local/hq/arms.json`, a rebuild
of `hq/HQ.md` through the vault's own `scripts/hq/morning.py` when that script exists, and a
Claude task queued in the runtime that still needs your approval and
`./hq worker --allow-external` to run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import core

DOMAIN = (core.ROOT / "..").resolve()
HOME = Path.home()
CLAUDE = HOME / ".claude"
STATE = core.ROOT / ".local" / "hq" / "arms.json"
CONFIG = core.ROOT / "system" / "arms.json"
RESOURCES = ("today", "recos", "skills", "routines", "memory", "usage")
CACHE_SECONDS = 60
COLD_AFTER_MINUTES = 60  # Claude Code writes 1 hour prompt caches; idle longer and the next message reloads
SMALL_SKILL_TOKENS = 800
_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


# ---------- small helpers ----------

def _slug(value: str, prefix: str = "") -> str:
    return (prefix + (re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:70] or "item"))[:90]


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line and not line.startswith(" "):
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta


def _ago(ts: float) -> str:
    minutes = int((time.time() - ts) / 60)
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


def _n(value: int) -> str:
    return f"{value:,}"


def _card(item_id, name, status, summary, fields, actions=None, copy=None, link=None):
    card = {"id": item_id, "type": "arms", "name": name, "status": status, "summary": summary,
            "provenance": "live", "fields": [[k, "" if v is None else str(v)] for k, v in fields],
            "actions": actions or []}
    if copy:
        card["copy"] = copy
        card["actions"] = ["copy"] + card["actions"]
    if link:
        card["link"] = link
        card["actions"] = ["open"] + card["actions"]
    return card


def _cached(key: str, build):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (time.time(), value)
    return value


def invalidate() -> None:
    with _lock:
        _cache.clear()


def config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("cart", [])
    data.setdefault("notes", [])
    return data


def save_state(data: dict[str, Any]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE)


# ---------- Claude Code session logs ----------

def _session_files(days: int) -> list[Path]:
    cutoff = time.time() - days * 86400
    root = CLAUDE / "projects"
    if not root.is_dir():
        return []
    return [p for p in root.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff]


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sessions(days: int = 30) -> list[dict[str, Any]]:
    """One summary per session log: token totals (deduplicated by message id), skills used, prompts."""
    def build():
        out = []
        for path in _session_files(days):
            seen, totals, skills, prompts = set(), Counter(), Counter(), []
            first = last = None
            last_context = 0
            title = None
            model = None
            try:
                handle = path.open(encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    kind = entry.get("type")
                    if kind == "custom-title":
                        title = entry.get("customTitle") or title
                    ts = _parse_ts(entry.get("timestamp"))
                    if ts:
                        first = ts if first is None else min(first, ts)
                        last = ts if last is None else max(last, ts)
                    message = entry.get("message") or {}
                    if kind == "user" and ts:
                        content = message.get("content")
                        text = content if isinstance(content, str) else " ".join(
                            c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
                        text = text.strip()
                        if text and not text.startswith("<") and not entry.get("isMeta"):
                            prompts.append((ts, text[:300]))
                    if kind != "assistant":
                        continue
                    model = message.get("model") or model
                    mid = message.get("id")
                    for block in message.get("content") or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                            call_id = block.get("id") or f"{mid}:{block.get('input')}"
                            if call_id not in seen:
                                seen.add(call_id)
                                skills[str((block.get("input") or {}).get("skill", ""))] += 1
                    usage = message.get("usage")
                    mid = message.get("id")
                    if not usage or mid in seen:
                        continue
                    seen.add(mid)
                    for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                        totals[key] += int(usage.get(key) or 0)
                    last_context = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0) \
                        + int(usage.get("cache_read_input_tokens") or 0)
                    if ts:
                        totals["day:" + datetime.fromtimestamp(ts).date().isoformat()] += sum(
                            int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
            if last is None:
                continue
            out.append({"id": path.stem, "project": path.parent.name, "title": title, "model": model,
                        "first": first, "last": last, "totals": totals, "skills": skills,
                        "prompts": prompts, "context": last_context})
        out.sort(key=lambda s: s["last"], reverse=True)
        return out
    return _cached(f"sessions:{days}", build)


def _cost(totals: Counter) -> float | None:
    rates = config().get("rates_per_million_tokens") or {}
    keys = {"input": "input_tokens", "cache_write": "cache_creation_input_tokens",
            "cache_read": "cache_read_input_tokens", "output": "output_tokens"}
    if not all(isinstance(rates.get(k), (int, float)) for k in keys):
        return None
    return sum(totals[v] * rates[k] / 1_000_000 for k, v in keys.items())


def _project_name(folder: str) -> str:
    parts = [p for p in folder.split("-") if p]
    return "/".join(parts[-2:]) if parts else folder


# ---------- Today ----------

def _hq_sections() -> tuple[dict[str, list[str]], str | None]:
    text = _read(DOMAIN / "hq" / "HQ.md")
    generated = _frontmatter(text).get("date")
    sections, current = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current and line.startswith("- "):
            sections[current].append(line[2:].strip())
    return sections, generated


def _clean(line: str) -> str:
    return re.sub(r"\[\[([^\]]+)\]\]", r"\1", line).replace("**", "")


def today_cards() -> tuple[list[dict], list[list[str]]]:
    sections, generated = _hq_sections()
    cards = []
    stale = None
    if generated:
        stale = (date.today() - date.fromisoformat(generated)).days
    cards.append(_card("hq-status", "HQ list " + ("is current" if stale == 0 else f"is {stale} days old" if stale else "not built"),
                       "ok" if stale == 0 else "warn",
                       "hq/HQ.md is rebuilt by scripts/hq/morning.py from TODO.md, shows/ and the Decision Log.",
                       [["Built for", generated or "never"], ["Source", "hq/HQ.md"]], ["rebuild-hq"]))
    due = sections.get("Due now (overdue or within 7 days)", [])
    waiting = sections.get("Waiting on you (blockers and rulings)", [])
    for i, line in enumerate(sections.get("Top 3", [])):
        cards.append(_card(f"top-{i}", _clean(line)[:140], "overdue" if "OVERDUE" in line else "top", "Top 3 today", [["Line", _clean(line)]]))
    for i, line in enumerate(due):
        if any(_clean(line)[:60] in c["name"] for c in cards):
            continue
        cards.append(_card(f"due-{i}", _clean(line)[:140], "overdue" if "OVERDUE" in line else "due", "Due within 7 days", [["Line", _clean(line)]]))
    for i, line in enumerate(waiting):
        cards.append(_card(f"wait-{i}", _clean(line)[:140], "waiting", "Waiting on your ruling", [["Line", _clean(line)]]))
    overdue = sum(1 for c in cards if c["status"] == "overdue")
    kpis = [["Overdue", str(overdue)], ["Due 7d", str(len(due))], ["Waiting on you", str(len(waiting))]]
    return cards, kpis


def rebuild_hq() -> str:
    folder = DOMAIN / "scripts" / "hq"
    if not (folder / "morning.py").is_file():
        raise ValueError("scripts/hq/morning.py is missing from the vault.")
    sys.path.insert(0, str(folder))
    saved, saved_db = sys.argv, os.environ.get("IA_PHOTOS_DB")
    # Opening Photos.sqlite from the server can sit forever behind a macOS privacy prompt, and Photos
    # jobs belong on the Air. Skip it unless system/arms.json says this Mac may read the library.
    if not config().get("rebuild_reads_photos"):
        os.environ["IA_PHOTOS_DB"] = str(core.ROOT / ".local" / "hq" / "no-photos-on-this-mac")
    try:
        import importlib
        for name in ("morning", "cdl", "build_works"):
            sys.modules.pop(name, None)
        module = importlib.import_module("morning")
        sys.argv = ["morning.py"]
        module.main()
    finally:
        sys.argv = saved
        if saved_db is None:
            os.environ.pop("IA_PHOTOS_DB", None)
        else:
            os.environ["IA_PHOTOS_DB"] = saved_db
        if str(folder) in sys.path:
            sys.path.remove(str(folder))
    invalidate()
    return "hq/HQ.md rebuilt for " + date.today().isoformat() + ". Commit hq/ when you are happy with it."


# ---------- Skills ----------

def skill_files() -> list[dict[str, Any]]:
    roots = [("vault agent", DOMAIN / "agent" / "skills"), ("the OS", DOMAIN / "os" / ".agents" / "skills"),
             ("your Claude", CLAUDE / "skills")]
    out = []
    for source, root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/SKILL.md")):
            text = _read(path)
            meta = _frontmatter(text)
            out.append({"name": meta.get("name") or path.parent.name, "description": meta.get("description", ""),
                        "model": meta.get("model"), "source": source, "path": path, "tokens": _tokens(text)})
    return out


def skills_cards() -> tuple[list[dict], list[list[str]]]:
    uses = Counter()
    for s in sessions(30):
        uses.update(s["skills"])
    state = load_state()
    parked = Counter(n["skill"] for n in state["notes"])
    cards = []
    files = sorted(skill_files(), key=lambda s: -s["tokens"])
    for s in files:
        rel = s["path"].relative_to(DOMAIN) if s["path"].is_relative_to(DOMAIN) else s["path"]
        suggestion = "None"
        if not s["model"] and s["tokens"] < SMALL_SKILL_TOKENS:
            suggestion = "Small and fixed-step: pin model: haiku in its frontmatter"
        prompt = f"Read {rel} and follow it exactly."
        cards.append(_card(_slug(s["source"] + "-" + s["name"], "skill-"), s["name"],
                           "pinned" if s["model"] else "unpinned", s["description"][:220],
                           [["Source", s["source"]], ["File", rel], ["Size", f"~{_n(s['tokens'])} tokens"],
                            ["Model", s["model"] or "not pinned (runs on the session model)"],
                            ["Uses, last 30 days", uses.get(s["name"], 0)], ["Parked notes", parked.get(s["name"], 0)],
                            ["Suggestion", suggestion]],
                           ["queue", "note"],
                           copy=f'cd {core.home_display(DOMAIN)} && claude -p "{prompt}"'))
    kpis = [["Skills", str(len(files))], ["Unpinned", str(sum(1 for s in files if not s["model"]))],
            ["Notes parked", str(len(state["notes"]))]]
    return cards, kpis


# ---------- Routines ----------

def routines_cards() -> tuple[list[dict], list[list[str]]]:
    modes = config().get("routine_modes", {})
    cards = []
    db = HOME / "PersonalAIHQ" / "runtime" / "os.sqlite3"
    module_schedules = []
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            module_schedules = [dict(zip(("id", "name", "workflow", "interval", "enabled", "next_run"), row))
                                 for row in con.execute("SELECT id,name,workflow,interval_seconds,enabled,next_run FROM schedules")]
            con.close()
        except sqlite3.Error:
            pass
    for row in module_schedules:
        mode = modes.get(row["workflow"], "unclassified")
        cards.append(_card("cur-" + row["id"][:12], row["name"], "enabled" if row["enabled"] else "paused",
                           f"OS schedule running workflow {row['workflow']}.",
                           [["Runs every", f"{row['interval'] // 3600} h" if row["interval"] >= 3600 else f"{row['interval'] // 60} min"],
                            ["Mode", mode], ["Next run", datetime.fromtimestamp(row["next_run"]).strftime("%b %d %H:%M")],
                            ["Needs", "./hq worker running"], ["Cost", "built-in, no model tokens"]]))
    for path in sorted((core.ROOT / "workflows").glob("*.json")):
        wf = json.loads(_read(path) or "{}")
        if any(r["workflow"] == wf.get("id") for r in module_schedules):
            continue
        cards.append(_card("wf-" + wf.get("id", path.stem), wf.get("name", path.stem) + " (not scheduled)", "idle",
                           "An OS workflow with no schedule. Add one in the dashboard, Schedules panel.",
                           [["Workflow", wf.get("id")], ["Mode", modes.get(wf.get("id"), "unclassified")]]))
    hermes = HOME / ".hermes"
    cards.append(_card("hermes-morning", "ia-morning brief (Hermes, weekdays 6:30)", "offline" if not hermes.is_dir() else "check",
                       "The morning brief in agent/skills/ia-morning. It runs only inside Hermes.",
                       [["Hermes", "not installed on this Mac" if not hermes.is_dir() else "installed, check `hermes cron`"],
                        ["Mode", modes.get("ia-morning", "active")], ["Setup", "agent/README.md"]]))
    tasks_dir = CLAUDE / "scheduled-tasks"
    claude_tasks = sorted(tasks_dir.glob("*/SKILL.md")) if tasks_dir.is_dir() else []
    for path in claude_tasks:
        meta = _frontmatter(_read(path))
        cards.append(_card("ct-" + path.parent.name, meta.get("name") or path.parent.name, "scheduled",
                           meta.get("description", "Claude scheduled task"),
                           [["Kind", "Claude Code scheduled task"], ["Mode", modes.get(path.parent.name, "unclassified")],
                            ["Cost", "not measured yet"]]))
    if not claude_tasks:
        cards.append(_card("ct-none", "No Claude scheduled tasks", "idle",
                           "Claude Code can run a prompt on a schedule on this Mac. Recos suggests which to add.", []))
    active = sum(1 for c in cards for f in c["fields"] if f[0] == "Mode" and f[1] == "active")
    passive = sum(1 for c in cards for f in c["fields"] if f[0] == "Mode" and f[1] == "passive")
    running = sum(1 for c in cards if c["status"] in {"enabled", "scheduled"})
    return cards, [["Running", str(running)], ["Active", str(active)], ["Passive", str(passive)]]


# ---------- Memory ----------

PATH_RE = re.compile(r"(?<![\w/])(~/[\w./\- ]+|/Users/[\w./\- ]+)")


def memory_files() -> list[dict[str, Any]]:
    files = [("global", CLAUDE / "CLAUDE.md")]
    for path in sorted(DOMAIN.glob("**/CLAUDE.md")):
        if any(part in {"target", "node_modules", ".git"} for part in path.parts):
            continue
        files.append(("vault", path))
    projects = CLAUDE / "projects"
    if projects.is_dir():
        for path in sorted(projects.glob("*/memory/MEMORY.md")):
            files.append(("memory", path))
    out = []
    for kind, path in files:
        if not path.is_file():
            continue
        text = _read(path)
        broken = []
        for raw in PATH_RE.findall(text):
            candidate = raw.rstrip(" .,:;)`'\"")
            candidate = candidate.split(" ")[0] if " " in candidate and not Path(candidate.replace("~", str(HOME), 1)).exists() else candidate
            resolved = Path(candidate.replace("~", str(HOME), 1))
            if "*" in candidate or "<" in candidate:
                continue
            if not resolved.exists() and candidate not in broken:
                broken.append(candidate)
        entries = sum(1 for line in text.splitlines() if line.startswith("- [")) if kind == "memory" else None
        out.append({"kind": kind, "path": path, "tokens": _tokens(text), "lines": text.count("\n") + 1,
                    "modified": path.stat().st_mtime, "broken": broken, "entries": entries})
    return out


def memory_cards() -> tuple[list[dict], list[list[str]]]:
    cards = []
    files = memory_files()
    for m in files:
        rel = m["path"].relative_to(DOMAIN) if m["path"].is_relative_to(DOMAIN) else str(m["path"]).replace(str(HOME), "~")
        name = {"global": "Global CLAUDE.md (every session)", "memory": "MEMORY.md: " + _project_name(m["path"].parent.parent.name)}.get(m["kind"], str(rel))
        fields = [["File", rel], ["Loaded", "every Claude Code session" if m["kind"] == "global" else
                                  "sessions in that project" if m["kind"] == "memory" else "when working in that folder"],
                  ["Size", f"~{_n(m['tokens'])} tokens, {m['lines']} lines"], ["Edited", _ago(m["modified"])]]
        if m["entries"] is not None:
            fields.append(["Memories", m["entries"]])
        fields.append(["Paths not found on this Mac", ", ".join(m["broken"][:6]) or "none"])
        if not m["broken"]:
            status, summary = "ok", "Every path it names exists."
        elif m["kind"] == "vault":
            # The vault is shared by both Macs; Air-only paths (~/work/, the runner) are expected here.
            status, summary = "check", f"{len(m['broken'])} path(s) are not on this Mac. Fine if they live on the Air."
        else:
            status, summary = "stale", f"{len(m['broken'])} path(s) point at nothing. Claude loads this file anyway."
        cards.append(_card(_slug(str(rel), "mem-"), name, status, summary, fields))
    stale = sum(1 for m in files if m["broken"] and m["kind"] != "vault")
    return cards, [["Files", str(len(files))], ["With dead paths", str(stale)],
                   ["Always-loaded tokens", "~" + _n(sum(m["tokens"] for m in files if m["kind"] == "global"))]]


# ---------- Usage ----------

def usage_cards() -> tuple[list[dict], list[list[str]]]:
    all_sessions = sessions(30)
    cards = []
    now = time.time()
    for label, days in (("Last 7 days", 7), ("Last 30 days", 30)):
        totals = Counter()
        cutoff = date.today() - timedelta(days=days - 1)
        for s in all_sessions:
            for key, value in s["totals"].items():
                if key.startswith("day:") and date.fromisoformat(key[4:]) >= cutoff:
                    totals["all"] += value
            if s["last"] >= now - days * 86400:
                for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
                    totals[key] += s["totals"][key]
        cost = _cost(totals)
        reads = totals["cache_read_input_tokens"]
        writes = totals["cache_creation_input_tokens"]
        cards.append(_card(_slug(label, "use-"), label, "ok", f"{_n(totals['all'])} tokens across Claude Code sessions on this Mac.",
                           [["Fresh input", _n(totals["input_tokens"])], ["Cache writes", _n(writes)], ["Cache reads", _n(reads)],
                            ["Output", _n(totals["output_tokens"])],
                            ["Cache hit share", f"{reads * 100 // max(reads + writes, 1)}%"],
                            ["API-price estimate", f"${cost:,.2f}" if cost is not None else "set rates in os/system/arms.json"]]))
    cold = 0
    for s in all_sessions[:10]:
        idle = (now - s["last"]) / 60
        is_cold = idle > COLD_AFTER_MINUTES
        cold += is_cold
        total = sum(s["totals"][k] for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
        cards.append(_card("sess-" + s["id"][:12], s["title"] or _project_name(s["project"]), "cold" if is_cold else "warm",
                           ("Cache is cold: your next message reloads about " if is_cold else "Cache is warm: the next message rereads about ")
                           + _n(s["context"]) + " tokens.",
                           [["Project", _project_name(s["project"])], ["Model", s["model"]], ["Last active", _ago(s["last"])],
                            ["Context now", _n(s["context"]) + " tokens"], ["Session total", _n(total) + " tokens"],
                            ["Skills used", ", ".join(f"{k} x{v}" for k, v in s["skills"].most_common(4)) or "none"]]))
    week = next((c for c in cards if c["id"] == "use-last-7-days"), None)
    return cards, [["Sessions 30d", str(len(all_sessions))], ["Cold caches", str(cold)],
                   ["7d tokens", week["summary"].split(" ")[0] if week else "0"]]


# ---------- Recos ----------

def _repeated_prompts(days: int = 7) -> list[tuple[str, int]]:
    cutoff = time.time() - days * 86400
    counts, sample = Counter(), {}
    for s in sessions(days):
        for ts, text in s["prompts"]:
            if ts < cutoff:
                continue
            words = re.findall(r"[a-z0-9']+", text.lower())[:6]
            if len(words) < 3:
                continue
            key = " ".join(words)
            counts[key] += 1
            sample.setdefault(key, text)
    return [(sample[k], n) for k, n in counts.most_common(5) if n >= 3]


def recos() -> list[dict[str, Any]]:
    out = []
    for m in memory_files():
        if m["broken"] and m["kind"] != "vault":
            where = str(m["path"]).replace(str(HOME), "~")
            out.append({"id": _slug(where, "fix-"), "kind": "Memory", "title": f"Fix {len(m['broken'])} dead path(s) in {where}",
                        "why": "Loaded into context, pointing at folders that do not exist: " + ", ".join(m["broken"][:4]),
                        "prompt": f"In {where}, these paths do not exist on this Mac: {', '.join(m['broken'][:8])}. "
                                  f"Show me a corrected version that maps to where the work lives now ({core.home_display(DOMAIN)}), keep it short, "
                                  "and wait for my OK before saving."})
    sections, generated = _hq_sections()
    tasks_dir = CLAUDE / "scheduled-tasks"
    scheduled = tasks_dir.is_dir() and any("morning" in p.name for p in tasks_dir.iterdir())
    if not (HOME / ".hermes").is_dir() and not scheduled:
        out.append({"id": "rebuild-hq", "kind": "Routine", "title": "Run the morning HQ every weekday",
                    "why": f"Nothing rebuilds hq/HQ.md on a schedule: Hermes is not installed here and no Claude task does it. "
                           f"Last built {generated or 'never'}.",
                    "prompt": f"Create a Claude scheduled task on this Mac for weekdays at 6:30am: in {core.home_display(DOMAIN)} run "
                              "`git pull --rebase --autostash`, then `python3 scripts/hq/morning.py`, then commit only hq/ with "
                              "message 'hq: morning run <date>'. Do not push. Passive: only notify me if something is OVERDUE."})
    for reco in core.local_config().get("extra_recos") or []:
        if isinstance(reco, dict) and reco.get("id") and reco.get("title") and reco.get("prompt"):
            out.append({**reco, "prompt": str(reco["prompt"]).replace("{domain}", core.home_display(DOMAIN)),
                        "why": str(reco.get("why", "")).replace("{domain}", core.home_display(DOMAIN))})
    small = [s for s in skill_files() if not s["model"] and s["tokens"] < SMALL_SKILL_TOKENS]
    if small:
        paths = [str(s["path"].relative_to(DOMAIN) if s["path"].is_relative_to(DOMAIN) else s["path"]) for s in small]
        out.append({"id": "pin-small-skills", "kind": "Skill", "title": f"Pin small skills to a small model ({len(small)} candidates)",
                    "why": "Short skills with no model pin run on the big session model: " + ", ".join(s["name"] for s in small[:8])
                           + ("..." if len(small) > 8 else ""),
                    "prompt": "These skills have no model pin and are under ~800 tokens:\n" + "\n".join("- " + p for p in paths)
                              + "\nFor each, decide if it is fixed-step work (logging, formatting, routing, running a script) or needs "
                                "judgment (grading, critique, curation). Add `model: haiku` only to the fixed-step ones. Show me the "
                                "list with your reason for each before changing anything."})
    state = load_state()
    parked = defaultdict(list)
    for note in state["notes"]:
        parked[note["skill"]].append(note["text"])
    for skill, notes in parked.items():
        bullet = "\n".join("- " + n for n in notes)
        out.append({"id": _slug(skill, "apply-"), "kind": "Skill", "title": f"Apply {len(notes)} parked note(s) to {skill}",
                    "why": "Feedback you parked with Note. Fold the principle in so the skill gets sharper, not longer.",
                    "prompt": f"Here is my parked feedback on the {skill} skill:\n{bullet}\nFind the principle behind each note, "
                              "fold it into the skill so it gets sharper and not longer, show me the diff, and keep a one-line log of why."})
    for text, n in _repeated_prompts():
        out.append({"id": _slug(text[:40], "rep-"), "kind": "Routine", "title": f"You asked this {n} times this week",
                    "why": text[:200], "prompt": f"I keep asking you: \"{text[:200]}\". Suggest a skill or a scheduled routine "
                                                 "that does this without me asking, and wait for my pick."})
    return out


def recos_cards() -> tuple[list[dict], list[list[str]]]:
    state = load_state()
    items = recos()
    in_cart = [r for r in items if r["id"] in state["cart"]]
    cards = []
    cart_prompt = "Set these up for me, one at a time, and show me each change before you save it:\n\n" + "\n\n".join(
        f"{i}. {r['title']}\n{r['prompt']}" for i, r in enumerate(in_cart, 1)) if in_cart else None
    cards.append(_card("cart", f"Your cart ({len(in_cart)})", "ready" if in_cart else "empty",
                       "Add recos, then copy the cart into Claude Code. Nothing is set up until you send it.",
                       [["In cart", ", ".join(r["title"] for r in in_cart) or "nothing yet"]],
                       ["cart-clear"] if in_cart else [], copy=cart_prompt))
    for r in items:
        chosen = r["id"] in state["cart"]
        cards.append(_card(r["id"], r["title"], "in-cart" if chosen else r["kind"].lower(), r["why"],
                           [["Kind", r["kind"]], ["Prompt", r["prompt"]]], ["cart-remove" if chosen else "cart-add"], copy=r["prompt"]))
    return cards, [["Suggestions", str(len(items))], ["In cart", str(len(in_cart))]]


# ---------- entry points ----------

BUILDERS = {"today": today_cards, "recos": recos_cards, "skills": skills_cards, "routines": routines_cards,
            "memory": memory_cards, "usage": usage_cards}


def domain(resource: str) -> tuple[list[dict], list[list[str]], str]:
    try:
        cards, kpis = BUILDERS[resource]()
        return cards, kpis, "live"
    except Exception as exc:  # One broken reader shows as unavailable; the rest keep working.
        return [_card(resource + "-error", "Could not read this view", "error", str(exc)[:300], [])], [["State", "error"]], "live"


def act(body: dict[str, Any]) -> dict[str, Any]:
    action = body.get("action")
    item = str(body.get("id") or "")[:120]
    state = load_state()
    if action == "cart-add":
        if item not in state["cart"]:
            state["cart"].append(item)
        save_state(state)
        return {"message": "Added to your cart."}
    if action == "cart-remove":
        state["cart"] = [i for i in state["cart"] if i != item]
        save_state(state)
        return {"message": "Removed from your cart."}
    if action == "cart-clear":
        state["cart"] = []
        save_state(state)
        return {"message": "Cart cleared."}
    if action == "note":
        from .brain import text as clean_text
        text = clean_text(body.get("text"), 2000)
        skill = clean_text(body.get("skill"), 120)
        if not text or not skill:
            raise ValueError("A note needs a skill and some text.")
        state["notes"].append({"skill": skill, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        save_state(state)
        return {"message": f"Parked for {skill}. Recos will offer to apply it."}
    if action == "rebuild-hq":
        return {"message": rebuild_hq()}
    raise ValueError("Unknown headquarters action.")
