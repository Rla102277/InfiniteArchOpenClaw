"""Portfolio site drafts: markdown updates in hq/os/drafts/site/ for the site's own pipeline."""
from __future__ import annotations

import re
from datetime import datetime

from .base import Connector, spec


class SiteConnector(Connector):
    id = "site"
    name = "Portfolio site"
    description = "Draft updates for the portfolio site (new work, show news) as markdown for the site's pipeline."
    ACTIONS = {"draft_update"}

    def status(self) -> dict:
        return {"ok": True, "needs": [], "detail": "Drafts land in hq/os/drafts/site/. Nothing is published from here."}

    def tools(self) -> list[dict]:
        return [spec("draft_update", "Draft a site update (title and markdown body).",
                     {"title": {"type": "string"}, "body": {"type": "string"}}, ["title", "body"])]

    def tool_draft_update(self, title: str, body: str):
        now = datetime.now()
        name = re.sub(r"[^a-z0-9]+", "-", f"{now:%Y-%m-%d}-{title}".lower()).strip("-")[:80] + ".md"
        folder = self.domain / "hq" / "os" / "drafts" / "site"
        folder.mkdir(parents=True, exist_ok=True)
        dash = "\u2014"
        title_text, body_text = str(title).replace(dash, ", "), str(body).replace(dash, ", ").strip()
        text = f"# {title_text}\n\n_Drafted {now:%Y-%m-%d %H:%M} by the OS. Not published._\n\n{body_text}\n"
        (folder / name).write_text(text, encoding="utf-8")
        return f"Drafted hq/os/drafts/site/{name}."
