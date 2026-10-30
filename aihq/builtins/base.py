"""The shape every connector has (built-in and MCP)."""
from __future__ import annotations

import json
from pathlib import Path


class Connector:
    id = "connector"
    name = "Connector"
    kind = "builtin"
    description = ""
    ACTIONS: set[str] = set()

    def __init__(self, domain: Path, config: dict | None = None):
        self.domain = Path(domain)
        self.config = dict(config or {})

    def status(self) -> dict:
        return {"ok": True, "detail": "ready", "needs": []}

    def tools(self) -> list[dict]:
        return []

    def call(self, tool: str, args: dict) -> str:
        method = getattr(self, f"tool_{tool}", None)
        if method is None or tool not in {t["name"] for t in self.tools()}:
            return f"{self.id} has no tool {tool}."
        result = method(**(args or {}))
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)

    def describe(self) -> dict:
        status = self.status()
        return {"id": self.id, "name": self.name, "kind": self.kind, "description": self.description,
                "status": status, "enabled": bool(self.config.get("enabled", True)),
                "tools": [{"name": t["name"], "description": t.get("description", ""), "action": t["name"] in self.ACTIONS}
                          for t in (self.tools() if status.get("ok") else [])]}


def spec(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {}, "required": required or []}}
