"""Instagram drafts. Publishing is not wired in v1 (the Graph API needs a
business account and a public image URL); a draft is a record on the `posts` board, and goes to
Telegram when the phone is set up, so the owner can post it from their phone."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .base import Connector, spec


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "post"


def _render(meta: dict, body: str) -> str:
    return "\n".join(["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in meta.items()] + ["---", ""]) + body.rstrip() + "\n"


class InstagramConnector(Connector):
    id = "instagram"
    name = "Instagram"
    description = "Draft posts: caption and image, on the Posts board. You post from your phone."
    ACTIONS = {"draft_post", "publish"}

    def status(self) -> dict:
        return {"ok": True, "needs": [], "detail": "Drafts only: they land on the Posts board (and your phone, when Telegram is set up)."}

    def tools(self) -> list[dict]:
        return [spec("draft_post", "Draft an Instagram post: caption (voice: direct, first person, no em dashes, a few hashtags at most) and an image path under hq/.",
                     {"caption": {"type": "string"}, "image_path": {"type": "string"}}, ["caption"]),
                spec("publish", "Publish a drafted post (not wired yet; says so).", {"id": {"type": "string"}}, ["id"])]

    def tool_draft_post(self, caption: str, image_path: str = ""):
        caption = str(caption).replace("\u2014", ", ").strip()
        now = datetime.now()
        record_id = f"{now:%Y%m%d-%H%M}-{_slug(caption[:40])}"
        folder = self.domain / "hq" / "os" / "records" / "posts"
        folder.mkdir(parents=True, exist_ok=True)
        meta = {"id": record_id, "app": "posts", "title": caption.splitlines()[0][:120] if caption else record_id,
                "status": "draft", "image": str(image_path or ""), "created": now.isoformat(timespec="seconds"),
                "updated": now.isoformat(timespec="seconds"), "source": "instagram"}
        (folder / f"{record_id}.md").write_text(_render(meta, caption), encoding="utf-8")
        sent = ""
        try:
            from .telegram import TelegramConnector
            from .. import connectors
            phone = TelegramConnector(self.domain, connectors.load_config(self.domain).get("builtin", {}).get("telegram", {}))
            if phone.status()["ok"]:
                if image_path:
                    phone.tool_send_photo(image_path, caption[:1000])
                else:
                    phone.tool_send("Instagram draft:\n\n" + caption)
                sent = " Sent to your phone to post."
        except Exception:
            sent = ""
        return f"Drafted post {record_id} on the Posts board.{sent}"

    def tool_publish(self, id: str):
        return "Publishing is not wired yet: copy the caption from the Posts board (or your phone) and post it from Instagram."
