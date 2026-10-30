"""Telegram, the phone. The connector sends; aihq/telegram_gateway.py listens and replies."""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .. import secrets
from .base import Connector, spec

SECRET = "telegram-bot-token"
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4000


class TelegramConnector(Connector):
    id = "telegram"
    name = "Telegram"
    description = "Your phone: bots message you, send photos, and ask for approvals with buttons."
    ACTIONS = {"send", "send_photo"}

    opener = staticmethod(urllib.request.urlopen)

    @property
    def chat_id(self) -> str:
        return str(self.config.get("chat_id") or "").strip()

    def status(self) -> dict:
        needs = []
        if not secrets.present(SECRET):
            needs.append(SECRET)
        if not self.chat_id:
            needs.append("chat_id")
        if not needs:
            return {"ok": True, "needs": [], "detail": f"ready, messages go to chat {self.chat_id}"}
        if SECRET in needs:
            return {"ok": False, "needs": needs, "detail": "Needs the bot token: message @BotFather, /newbot, paste the token here (Connectors > Telegram)."}
        return {"ok": False, "needs": needs, "detail": "Token saved. Now send /start to your bot from your phone so the OS learns your chat."}

    def tools(self) -> list[dict]:
        return [spec("send", "Message the owner on their phone. Short, verdict first.", {"text": {"type": "string"}}, ["text"]),
                spec("send_photo", "Send the owner a photo from the repo (a path under hq/) with a caption.",
                     {"path": {"type": "string"}, "caption": {"type": "string"}}, ["path"])]

    # the low-level call the gateway uses too ------------------------------------------------
    def api(self, method: str, params: dict | None = None, files: dict | None = None, timeout: float = 35):
        token = secrets.get(SECRET)
        if not token:
            raise ValueError(self.status()["detail"])
        url = API.format(token=token, method=method)
        if files:
            boundary = uuid.uuid4().hex
            body = bytearray()
            for key, value in (params or {}).items():
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8")
            for key, (filename, data) in files.items():
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{filename}\"\r\n"
                         f"Content-Type: {mime}\r\n\r\n").encode("utf-8") + data + b"\r\n"
            body += f"--{boundary}--\r\n".encode("utf-8")
            request = urllib.request.Request(url, data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            request = urllib.request.Request(url, data=json.dumps(params or {}).encode("utf-8"), headers={"Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except (ValueError, OSError):
                raise ValueError(f"Telegram {method}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ValueError(f"Telegram {method}: {exc.__class__.__name__}") from None
        if not payload.get("ok"):
            raise ValueError(f"Telegram {method}: {payload.get('description', 'refused')}")
        return payload.get("result")

    def _need_chat(self) -> str:
        if not self.chat_id:
            raise ValueError(self.status()["detail"])
        return self.chat_id

    def tool_send(self, text: str):
        chat = self._need_chat()
        text = str(text).replace("\u2014", ", ")
        chunks = [text[i:i + LIMIT] for i in range(0, len(text), LIMIT)] or [""]
        for chunk in chunks:
            self.api("sendMessage", {"chat_id": chat, "text": chunk, "disable_web_page_preview": True})
        return f"Sent to your phone ({len(text)} chars)."

    def tool_send_photo(self, path: str, caption: str = ""):
        chat = self._need_chat()
        hq = (self.domain / "hq").resolve()
        target = (self.domain / path).resolve()
        if not target.is_relative_to(hq) or not target.is_file():
            return "send_photo: the photo must be a file under hq/ (for example hq/os/uploads/...)."
        if target.stat().st_size > 10_000_000:
            return "send_photo: that file is over 10 MB."
        self.api("sendPhoto", {"chat_id": chat, "caption": str(caption).replace("\u2014", ", ")[:1000]},
                 files={"photo": (target.name, target.read_bytes())}, timeout=90)
        return f"Photo sent: {target.name}."
