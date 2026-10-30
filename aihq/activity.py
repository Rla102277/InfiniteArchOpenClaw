"""Activity: every outside action an agent took or queued, one JSON line each.

`hq/os/activity/YYYY-MM-DD.jsonl` (synced to GitHub with the rest of hq/) plus one hq/log line.
The Overview reads `recent()` and shows `sentence()` for each.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from . import core

STATUSES = {"done", "queued", "failed"}
MAX_TEXT = 400


def _dir(domain: Path) -> Path:
    return Path(domain) / "hq" / "os" / "activity"


def _clip(value, limit=MAX_TEXT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.replace("\u2014", ", ").split())
    return text[:limit]


def record(domain: Path, who: str, connector: str, tool: str, args_summary="", result_summary="", status="done") -> dict:
    status = status if status in STATUSES else "done"
    now = datetime.now()
    entry = {"time": now.isoformat(timespec="seconds"), "who": _clip(who, 80), "connector": _clip(connector, 80),
             "tool": _clip(tool, 80), "args": _clip(args_summary), "result": _clip(result_summary), "status": status}
    folder = _dir(domain)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log = Path(domain) / "hq" / "log" / f"{now:%Y-%m-%d}.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"- {now:%H:%M} · {entry['who']} · {status}: {connector}.{tool} {entry['args'][:120]} · hq/os/activity/{now:%Y-%m-%d}.jsonl\n")
    return entry


def recent(domain: Path, days: int = 7, limit: int = 200) -> list[dict]:
    folder = _dir(domain)
    out: list[dict] = []
    if not folder.is_dir():
        return out
    today = date.today()
    for offset in range(max(1, days)):
        path = folder / f"{today - timedelta(days=offset):%Y-%m-%d}.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(out) >= limit:
                return out
    return out


# (past tense for what happened, base form for "wants to ..." and "tried to ...")
VERBS = {
    ("calendar", "create_event"): ("put {what} on your calendar", "put {what} on your calendar"),
    ("gmail", "send"): ("emailed {what}", "email {what}"),
    ("gmail", "draft"): ("drafted an email {what}", "draft an email {what}"),
    ("telegram", "send"): ("messaged you {what}", "message you {what}"),
    ("telegram", "send_photo"): ("sent you a photo {what}", "send you a photo {what}"),
    ("instagram", "draft_post"): ("drafted a post {what}", "draft a post {what}"),
    ("site", "draft_update"): ("drafted a site update {what}", "draft a site update {what}"),
}


def _who(value: str, domain: Path | None = None) -> str:
    if value.startswith("app:"):
        app_id = value[4:]
        if domain is not None:  # the bot's own name (Show Scout), not its id (art-shows)
            try:
                spec = json.loads((Path(domain) / "hq" / "os" / "apps" / app_id / "app.json").read_text(encoding="utf-8"))
                if spec.get("name"):
                    return str(spec["name"])
            except (OSError, ValueError):
                pass
        return app_id.replace("-", " ").title()
    return {"operator": "The Operator", core.owner_id(): "You"}.get(value, value or "An agent")


def sentence(entry: dict, domain: Path | None = None) -> str:
    who = _who(str(entry.get("who", "")), domain)
    connector, tool = str(entry.get("connector", "")), str(entry.get("tool", ""))
    what = str(entry.get("args", "")).strip()
    what = f"({what[:90]})" if what else ""
    done, base = VERBS.get((connector, tool), (f"used {connector}.{tool} {{what}}", f"use {connector}.{tool} {{what}}"))
    done, base = done.format(what=what).strip(), base.format(what=what).strip()
    status = entry.get("status")
    if status == "queued":
        return f"{who} wants to {base}: waiting for your approval."
    if status == "failed":
        return f"{who} tried to {base}, and it failed: {str(entry.get('result', ''))[:120]}"
    return f"{who} {done}."
