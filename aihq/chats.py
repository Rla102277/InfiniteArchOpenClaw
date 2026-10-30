"""Conversations in the brain: one Markdown file per thread under hq/os/chats/.

A thread is readable in Obsidian and in Git. Images are stored once under hq/os/uploads/ and
referenced by relative path. history() turns a thread back into Anthropic-format messages for
the model, within a character budget, with the most recent photos as image blocks and older ones
as a note. No model calls happen here; the runtime asks the model for a summary when
needs_summary() says so and stores it with set_summary().
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from . import core
from .basetools import frontmatter, no_dashes, render, slug

TURN = re.compile(r"^## (?P<who>[^\n·]+?) · (?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\s*$", re.MULTILINE)
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
DETAILS = re.compile(r"(?s)\n?<details>.*?</details>\n?")
MIME = {"image/jpeg": "jpg", "image/png": "png"}
UPLOAD_CAP = 5 * 1024 * 1024
RECENT_IMAGES = 4
USER_LABEL = core.owner_name()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.replace(path)


class Chats:
    def __init__(self, domain):
        self.domain = Path(domain).resolve()
        self.hq = self.domain / "hq"
        self.root = self.hq / "os" / "chats"
        self.uploads = self.hq / "os" / "uploads"

    # paths ----------------------------------------------------------------------
    def path(self, id: str) -> Path:
        safe = re.sub(r"[^a-z0-9-]", "", str(id).lower())
        if not safe:
            raise ValueError("Bad thread id.")
        target = (self.root / f"{safe}.md").resolve()
        if self.root.resolve() not in target.parents:
            raise PermissionError("Thread path escaped hq/os/chats.")
        return target

    def _read(self, id: str) -> tuple[dict, str]:
        path = self.path(id)
        if not path.is_file():
            raise ValueError(f"No thread {id}.")
        return frontmatter(path.read_text(encoding="utf-8", errors="replace"))

    # listing ---------------------------------------------------------------------
    def list(self, app: str | None = None) -> list[dict]:
        out = []
        if self.root.is_dir():
            for path in self.root.glob("*.md"):
                meta, _ = frontmatter(path.read_text(encoding="utf-8", errors="replace"))
                if app is not None and str(meta.get("app") or "") != app:
                    continue
                out.append({"id": meta.get("id", path.stem), "title": meta.get("title", path.stem), "agent": meta.get("agent", ""),
                            "app": meta.get("app") or "", "created": meta.get("created", ""), "updated": meta.get("updated", ""),
                            "messages": int(meta.get("messages", 0) or 0)})
        out.sort(key=lambda item: str(item.get("updated", "")), reverse=True)
        return out

    def get(self, id: str) -> dict:
        meta, body = self._read(id)
        return {**meta, "turns": self.turns(body)}

    @staticmethod
    def turns(body: str) -> list[dict]:
        out = []
        matches = list(TURN.finditer(body))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
            text = body[match.end():end].strip()
            images = IMAGE.findall(text)
            clean_text = DETAILS.sub("\n", IMAGE.sub("", text)).strip()
            who = match.group("who").strip()
            out.append({"who": who, "role": "user" if who == USER_LABEL else "assistant", "when": match.group("when"),
                        "text": clean_text, "images": images, "raw": text})
        return out

    # creating and appending ---------------------------------------------------------------
    def create(self, title: str = "New thread", agent: str = "operator", app: str | None = None) -> dict:
        title = no_dashes(" ".join(str(title or "New thread").split()))[:120] or "New thread"
        base = f"{date.today():%Y%m%d}-{slug(title)[:40]}"
        id, n = base, 1
        while self.path(id).exists():
            n += 1
            id = f"{base}-{n}"
        now = datetime.now().isoformat(timespec="seconds")
        meta = {"id": id, "title": title, "agent": str(agent or "operator"), "app": app or "", "created": now, "updated": now,
                "messages": 0, "summary": ""}
        _atomic_write(self.path(id), render(meta, f"# {title}\n"))
        return meta

    def save_image(self, data: bytes, mime: str, name: str = "") -> str:
        """Store one image under hq/os/uploads; returns the path relative to the repo."""
        ext = MIME.get(str(mime).lower().split(";")[0].strip())
        if not ext:
            raise ValueError("Only JPEG and PNG images are accepted.")
        if len(data) > UPLOAD_CAP:
            raise ValueError("Images over 5 MB are not accepted; export a smaller JPEG.")
        digest = hashlib.sha256(data).hexdigest()[:16]
        path = self.uploads / f"{digest}.{ext}"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return path.relative_to(self.domain).as_posix()

    def append(self, id: str, role: str, text: str, images: list | None = None, tool_calls: list | None = None,
               usage: dict | None = None, who: str | None = None) -> dict:
        """Append a turn. `images` are dicts {data: bytes | base64 str, mime, name} or repo-relative paths."""
        meta, body = self._read(id)
        label = who or (USER_LABEL if role == "user" else str(meta.get("agent") or "assistant"))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"\n## {label} · {stamp}\n", no_dashes(str(text or "")).rstrip()]
        stored = []
        for image in images or []:
            if isinstance(image, str):
                rel = image
            else:
                data = image.get("data")
                if isinstance(data, str):
                    data = base64.b64decode(data)
                rel = self.save_image(data, image.get("mime", "image/jpeg"), image.get("name", ""))
            stored.append(rel)
            rel_from_chat = Path("..") / Path(rel).relative_to("hq/os") if rel.startswith("hq/os/") else Path(rel)
            lines.append(f"\n![photo]({rel_from_chat.as_posix()})")
        if tool_calls:
            lines.append("\n<details><summary>Tool calls</summary>\n")
            for call in tool_calls:
                lines.append(f"- `{call.get('name')}` {json.dumps(call.get('args', {}), ensure_ascii=False)[:300]}")
            if usage:
                lines.append(f"\ntokens: {usage.get('input', 0)} in / {usage.get('output', 0)} out")
            lines.append("\n</details>")
        body = body.rstrip() + "\n" + "\n".join(lines) + "\n"
        meta["messages"] = int(meta.get("messages", 0) or 0) + 1
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        if role == "user" and meta.get("title") in ("New thread", "", None) and str(text or "").strip():
            meta["title"] = no_dashes(" ".join(str(text).split()))[:60]
        _atomic_write(self.path(id), render(meta, body))
        return {"id": id, "role": role, "who": label, "text": text, "images": stored, "when": stamp, "messages": meta["messages"]}

    # history for the model -----------------------------------------------------------------
    def _image_block(self, rel: str) -> dict | None:
        rel = rel.replace("../", "hq/os/", 1) if rel.startswith("../") else rel
        path = (self.domain / rel).resolve()
        if self.uploads.resolve() not in path.parents or not path.is_file():
            return None
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                            "data": base64.b64encode(path.read_bytes()).decode("ascii")}}

    def history(self, id: str, limit_chars: int = 24000, with_images: bool = True) -> list[dict]:
        meta, body = self._read(id)
        turns = self.turns(body)
        # newest first until the budget is spent, then flip back
        kept, used = [], 0
        for turn in reversed(turns):
            cost = len(turn["text"]) + 200
            if kept and used + cost > limit_chars:
                break
            kept.append(turn)
            used += cost
        kept.reverse()
        older = turns[: len(turns) - len(kept)]
        image_budget = RECENT_IMAGES
        messages: list[dict] = []
        for turn in kept:
            blocks = []
            if turn["role"] == "user":
                for rel in turn["images"]:
                    block = self._image_block(rel) if (with_images and image_budget > 0) else None
                    if block:
                        blocks.append(block)
                        image_budget -= 1
                    else:
                        blocks.append({"type": "text", "text": "[photo omitted]"})
            if turn["text"]:
                blocks.append({"type": "text", "text": turn["text"]})
            if not blocks:
                blocks.append({"type": "text", "text": "(no text)"})
            messages.append({"role": turn["role"], "content": blocks})
        # the oldest kept images beyond the budget: already handled above; now the summary
        if older:
            summary = str(meta.get("summary") or "").strip()
            if not summary:
                summary = " / ".join(f"{t['who']}: {t['text'][:120]}" for t in older if t["text"])[:1500]
            messages.insert(0, {"role": "user", "content": [{"type": "text", "text": f"Earlier in this thread (summary): {summary}"}]})
            if messages and len(messages) > 1 and messages[1]["role"] == "user":
                messages.insert(1, {"role": "assistant", "content": [{"type": "text", "text": "Understood. Continuing the thread."}]})
        # Anthropic wants alternation starting with user
        out: list[dict] = []
        for message in messages:
            if out and out[-1]["role"] == message["role"]:
                out[-1]["content"] = out[-1]["content"] + message["content"]
            else:
                out.append(message)
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": [{"type": "text", "text": "(thread continues)"}]})
        return out

    def needs_summary(self, id: str, limit_chars: int = 24000) -> bool:
        meta, body = self._read(id)
        total = sum(len(t["text"]) + 200 for t in self.turns(body))
        return total > limit_chars and not str(meta.get("summary") or "").strip()

    def set_summary(self, id: str, text: str) -> None:
        meta, body = self._read(id)
        meta["summary"] = no_dashes(" ".join(str(text).split()))[:3000]
        _atomic_write(self.path(id), render(meta, body))

    def older_text(self, id: str, limit_chars: int = 24000) -> str:
        """The turns history() would drop, as text, for the runtime to summarize."""
        _, body = self._read(id)
        turns = self.turns(body)
        used, keep = 0, 0
        for turn in reversed(turns):
            cost = len(turn["text"]) + 200
            if keep and used + cost > limit_chars:
                break
            keep += 1
            used += cost
        return "\n\n".join(f"{t['who']}: {t['text']}" for t in turns[: len(turns) - keep])

    def rename(self, id: str, title: str) -> dict:
        meta, body = self._read(id)
        meta["title"] = no_dashes(" ".join(str(title).split()))[:120] or meta.get("title")
        _atomic_write(self.path(id), render(meta, body))
        return meta
