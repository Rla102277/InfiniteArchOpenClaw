"""Telegram: the OS in the owner's pocket.

One long-poll loop (getUpdates, 25 s) in its own daemon thread, started by the OS when the
`telegram-bot-token` secret exists. Only allowlisted Telegram user ids are served; anyone else
who sends /start is told their id (so the owner can allow themselves) and nothing more.

- text            -> the Operator, in a thread per day (`telegram-YYYYMMDD`), answer sent back
- a photo         -> the module app's vision agent, with the caption as the question
- /today, /inbox  -> Top 3, and the first open items
- /<command>      -> the bot that declares that telegram trigger runs now
- approvals       -> every open inbox approval is sent once with Approve / Reject buttons; the
                     tap runs the same decision path as the dashboard, then the message is
                     edited to the result
- notify(text)    -> other code (bots) sends the owner a message

Config lives in hq/os/connectors.json under builtin.telegram: {"chat_id": "...", "allow": [ids]}.
The update offset and the approvals already announced live in the OS private dir.
Stdlib only. Nothing here raises past the loop: a bad update is logged and skipped.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

API = "https://api.telegram.org"
SECRET = "telegram-bot-token"
MAX_TEXT = 4000
POLL_TIMEOUT = 25
CONFIG = Path("hq") / "os" / "connectors.json"
DOMAIN = Path(__file__).resolve().parents[2]

_thread: threading.Thread | None = None
_stop = threading.Event()
_lock = threading.Lock()


# ---------- config and secrets ----------

def token() -> str | None:
    try:
        from . import secrets as os_secrets
        value = os_secrets.get(SECRET)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None


def _config_path(domain: Path) -> Path:
    return Path(domain) / CONFIG


def config(domain: Path) -> dict:
    try:
        data = json.loads(_config_path(domain).read_text(encoding="utf-8"))
        telegram = ((data.get("builtin") or {}).get("telegram") or {}) if isinstance(data, dict) else {}
        return telegram if isinstance(telegram, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(domain: Path, changes: dict) -> dict:
    path = _config_path(domain)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    builtin = data.setdefault("builtin", {})
    telegram = builtin.setdefault("telegram", {})
    telegram.update(changes)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return telegram


def allowed_ids(domain: Path) -> set[str]:
    return {str(x) for x in (config(domain).get("allow") or [])}


def _state_path(private_dir: Path | None) -> Path:
    base = Path(private_dir) if private_dir else Path(os.environ.get("AIHQ_HOME", str(Path.home() / "PersonalAIHQ")))
    return base / "telegram" / "state.json"


def _load_state(private_dir) -> dict:
    try:
        data = json.loads(_state_path(private_dir).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(private_dir, data: dict) -> None:
    path = _state_path(private_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------- the Bot API ----------

class BotAPI:
    """Thin HTTPS client. Tests swap it for a fake with the same call(method, **params)."""

    def __init__(self, bot_token: str, timeout: int = POLL_TIMEOUT + 10):
        self.token = bot_token
        self.timeout = timeout

    def call(self, method: str, **params):
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode("utf-8")
        request = urllib.request.Request(f"{API}/bot{self.token}/{method}", data=data, method="POST",
                                         headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise ValueError(f"Telegram {method} {error.code}: {detail}")
        if not payload.get("ok"):
            raise ValueError(f"Telegram {method}: {payload.get('description', 'failed')}")
        return payload.get("result")

    def download(self, file_path: str) -> bytes:
        with urllib.request.urlopen(f"{API}/file/bot{self.token}/{urllib.parse.quote(file_path)}", timeout=60) as response:
            return response.read(20 * 1024 * 1024)


def split(text: str, size: int = MAX_TEXT) -> list[str]:
    text = str(text or "").strip() or "(no answer)"
    parts = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


# ---------- the gateway ----------

class Gateway:
    def __init__(self, runtime=None, domain: Path | None = None, api=None, private_dir: Path | None = None,
                 log=None):
        self.runtime = runtime
        self.domain = Path(domain or getattr(runtime, "domain", None) or DOMAIN)
        self.private_dir = private_dir or getattr(runtime, "home", None)
        if api is None:
            bot_token = token()
            if not bot_token:
                raise ValueError("No Telegram bot token. Create a bot with @BotFather, then save its token in "
                                 "Connectors (or `./hq secret set telegram-bot-token`).")
            api = BotAPI(bot_token)
        self.api = api
        self.state = _load_state(self.private_dir)
        self.log = log or (lambda what: None)

    # -- sending --
    def chat_id(self) -> str | None:
        cid = config(self.domain).get("chat_id")
        return str(cid) if cid else None

    def send(self, text: str, chat_id: str | None = None, reply_markup: dict | None = None) -> list:
        target = chat_id or self.chat_id()
        if not target:
            raise ValueError("Telegram has no chat yet. Send /start to the bot from your phone first.")
        sent = []
        for i, part in enumerate(split(text)):
            sent.append(self.api.call("sendMessage", chat_id=target, text=part,
                                      reply_markup=reply_markup if i == len(split(text)) - 1 else None,
                                      disable_web_page_preview=True))
        return sent

    # -- one poll --
    def poll_once(self, timeout: int = POLL_TIMEOUT) -> int:
        updates = self.api.call("getUpdates", offset=int(self.state.get("offset", 0)) or None, timeout=timeout,
                                allowed_updates=["message", "callback_query"]) or []
        for update in updates:
            self.state["offset"] = int(update.get("update_id", 0)) + 1
            try:
                self.handle(update)
            except Exception as exc:  # one bad update never stops the loop
                self.log(f"telegram update failed: {str(exc)[:200]}")
        if updates:
            _save_state(self.private_dir, self.state)
        try:
            self.announce_approvals()
        except Exception as exc:
            self.log(f"telegram approvals failed: {str(exc)[:200]}")
        return len(updates)

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            return self.handle_callback(update["callback_query"])
        message = update.get("message") or {}
        user = str((message.get("from") or {}).get("id", ""))
        chat = str((message.get("chat") or {}).get("id", ""))
        text = str(message.get("text") or message.get("caption") or "").strip()
        allow = allowed_ids(self.domain)
        if user not in allow:
            if text.startswith("/start"):
                self.api.call("sendMessage", chat_id=chat, text=(
                    f"This is a private bot. Your Telegram id is {user}. If you own this OS, add it under "
                    "Connectors > Telegram (allowed ids) and send /start again."))
            self.log(f"telegram: ignored a message from unallowed user {user}")
            return
        if not self.chat_id() or self.chat_id() != chat:
            save_config(self.domain, {"chat_id": chat})
        if text.startswith("/"):
            return self.handle_command(text, chat)
        if message.get("photo"):
            return self.handle_photo(message, text, chat)
        if not text:
            return
        reply = self._chat(text, agent="operator")
        self.send(reply, chat)

    def handle_command(self, text: str, chat: str) -> None:
        command = text.split()[0].split("@")[0].lower()
        rest = text[len(text.split()[0]):].strip()
        if command in ("/start", "/help"):
            self.send("Connected. Write to me like you write to the OS. /today for the Top 3, /inbox for what waits on you, "
                      + ", ".join(sorted(self._commands())) + (" to run a bot." if self._commands() else ""), chat)
            return
        if command == "/today":
            self.send(self._today(), chat)
            return
        if command == "/inbox":
            self.send(self._inbox(), chat)
            return
        app_id = self._commands().get(command)
        if app_id:
            self.send(f"Running {app_id} now.", chat)
            result = self._run_bot(app_id, rest)
            if isinstance(result, dict) and result.get("error"):
                self.send(f"{app_id} failed: {result['error'][:500]}", chat)
            elif isinstance(result, dict) and not any(str(c.get("name", "")).startswith("telegram.") for c in result.get("tool_calls", []) or []):
                changes = result.get("changes") or {}
                self.send(f"{app_id}: {changes.get('new', 0)} new, {changes.get('updated', 0)} updated.\n\n{(result.get('answer') or '').strip()[:3000]}", chat)
            return
        self.send(f"I do not know {command}. Try /today, /inbox" + (", " + ", ".join(sorted(self._commands())) if self._commands() else "") + ".", chat)

    def handle_photo(self, message: dict, caption: str, chat: str) -> None:
        photos = message.get("photo") or []
        biggest = max(photos, key=lambda p: int(p.get("file_size", 0) or 0)) if photos else None
        if not biggest:
            return
        info = self.api.call("getFile", file_id=biggest.get("file_id"))
        data = self.api.download(info.get("file_path", ""))
        mime = mimetypes.guess_type(info.get("file_path", ""))[0] or "image/jpeg"
        images = [{"name": Path(info.get("file_path", "photo.jpg")).name, "mime": mime,
                   "data_base64": base64.b64encode(data).decode("ascii")}]
        reply = self._chat(caption or "Grade this frame against the Protocol. Verdict first.", agent="critic", images=images)
        self.send(reply, chat)

    def handle_callback(self, query: dict) -> None:
        user = str((query.get("from") or {}).get("id", ""))
        data = str(query.get("data") or "")
        message = query.get("message") or {}
        if user not in allowed_ids(self.domain):
            self.api.call("answerCallbackQuery", callback_query_id=query.get("id"), text="Not allowed.")
            return
        m = re.fullmatch(r"(ap|rj):([\w-]{1,64})", data)
        if not m:
            self.api.call("answerCallbackQuery", callback_query_id=query.get("id"), text="Unknown button.")
            return
        status = "approved" if m.group(1) == "ap" else "rejected"
        try:
            result = self._decide(m.group(2), status)
            outcome = ("Approved" if status == "approved" else "Rejected") + (f": {result}" if isinstance(result, str) and result else "")
        except Exception as exc:
            outcome = f"Could not {status[:-1]}: {str(exc)[:300]}"
        self.api.call("answerCallbackQuery", callback_query_id=query.get("id"), text=outcome[:180])
        if message.get("message_id"):
            original = str(message.get("text") or "")
            self.api.call("editMessageText", chat_id=(message.get("chat") or {}).get("id"), message_id=message["message_id"],
                          text=(original + f"\n\n{outcome}")[:MAX_TEXT])

    # -- approvals out --
    def announce_approvals(self) -> int:
        if not self.chat_id():
            return 0
        announced = set(self.state.get("announced", []))
        sent = 0
        for item in _open_approvals(self.domain):
            if item["id"] in announced:
                continue
            body = str(item.get("body", "")).strip()
            text = f"Needs your OK: {item.get('title', item['id'])}\n\n{body[:1500]}"
            keyboard = {"inline_keyboard": [[{"text": "Approve", "callback_data": f"ap:{item['id']}"},
                                             {"text": "Reject", "callback_data": f"rj:{item['id']}"}]]}
            self.send(text, reply_markup=keyboard)
            announced.add(item["id"])
            sent += 1
        if sent:
            self.state["announced"] = sorted(announced)[-500:]
            _save_state(self.private_dir, self.state)
        return sent

    # -- the OS behind it (runtime when present) --
    def _chat(self, text: str, agent: str = "operator", images: list | None = None) -> str:
        if self.runtime is None:
            raise ValueError("The gateway needs the OS runtime to answer.")
        thread = f"telegram-{datetime.now():%Y%m%d}" if agent == "operator" else None
        known = self.state.setdefault("threads", {})
        payload = {"text": text, "agent": agent, "images": images or []}
        if thread and known.get(thread):
            payload["thread"] = known[thread]
        result = self.runtime.action({"action": "chat.send", **payload})
        if thread and isinstance(result, dict) and result.get("thread"):
            known[thread] = result["thread"]
            _save_state(self.private_dir, self.state)
        message = (result or {}).get("message") if isinstance(result, dict) else None
        return (message or {}).get("text") if isinstance(message, dict) else str(message or "")

    def _decide(self, item_id: str, status: str):
        if self.runtime is None:
            raise ValueError("The gateway needs the OS runtime to decide.")
        result = self.runtime.action({"action": "inbox.set", "id": item_id, "status": status, "note": "Decided from Telegram."})
        if isinstance(result, dict):
            return result.get("message") or result.get("summary") or ""
        return ""

    def _commands(self) -> dict:
        try:
            from . import agentapps
            return agentapps.commands(self.domain)
        except Exception:
            return {}

    def _run_bot(self, app_id: str, extra: str = ""):
        if self.runtime is not None:
            return self.runtime.action({"action": "apps.run", "id": app_id})
        from . import agentapps
        return agentapps.run(self.domain, app_id)

    def _today(self) -> str:
        hq = self.domain / "hq" / "HQ.md"
        try:
            text = hq.read_text(encoding="utf-8")
        except OSError:
            return "No HQ yet. The morning routine builds it."
        m = re.search(r"## Top 3\s*\n(.*?)(?:\n## |\Z)", text, re.S)
        top = (m.group(1).strip() if m else "Nothing ranked.").replace("**", "")
        return "Today, top 3:\n" + top

    def _inbox(self) -> str:
        items = _open_items(self.domain)
        if not items:
            return "Inbox is empty."
        lines = [f"- [{i.get('kind', 'note')}] {i.get('title', i['id'])}" for i in items[:5]]
        return f"{len(items)} waiting. First {min(5, len(items))}:\n" + "\n".join(lines)


def _inbox_items(domain: Path) -> list[dict]:
    root = Path(domain) / "hq" / "os" / "inbox"
    out = []
    if not root.is_dir():
        return out
    from .agentapps import frontmatter
    for path in sorted(root.glob("*.md")):
        try:
            meta, body = frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if meta.get("id"):
            out.append({**meta, "body": body})
    return out


def _open_items(domain: Path) -> list[dict]:
    return [i for i in _inbox_items(domain) if i.get("status") == "open"]


def _open_approvals(domain: Path) -> list[dict]:
    return [i for i in _open_items(domain) if i.get("kind") == "approval"]


# ---------- what the rest of the OS calls ----------

def notify(text: str, domain: Path | None = None, api=None) -> bool:
    """Send the owner a message if Telegram is set up. False (never an exception) when it is not."""
    domain = Path(domain or DOMAIN)
    try:
        cid = config(domain).get("chat_id")
        if not cid:
            return False
        if api is None:
            bot_token = token()
            if not bot_token:
                return False
            api = BotAPI(bot_token, timeout=20)
        for part in split(text):
            api.call("sendMessage", chat_id=str(cid), text=part, disable_web_page_preview=True)
        return True
    except Exception:
        return False


def start(runtime, log=None) -> bool:
    """Start the long-poll thread once, if there is a token. Returns whether it is running."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        if not token():
            return False
        try:
            gateway = Gateway(runtime, log=log)
        except Exception as exc:
            if log:
                log(f"telegram gateway did not start: {exc}")
            return False
        _stop.clear()

        def loop():
            backoff = 5
            while not _stop.is_set():
                try:
                    gateway.poll_once()
                    backoff = 5
                except Exception as exc:
                    if log:
                        log(f"telegram poll failed: {str(exc)[:200]}")
                    _stop.wait(backoff)
                    backoff = min(backoff * 2, 300)

        _thread = threading.Thread(target=loop, name="telegram-gateway", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    _stop.set()


def running() -> bool:
    return _thread is not None and _thread.is_alive()
