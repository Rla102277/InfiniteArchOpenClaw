"""Swappable execution adapters. The headquarters shell reports availability only.

Looking up an executable is not a heartbeat, and this module never starts a process
from chat text.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterSpec:
    id: str
    label: str
    command: str | None
    locality: str
    network: str
    role: str


# Order is the owner-facing catalog. local-hq stays inside this process.
ADAPTERS: tuple[AdapterSpec, ...] = (
    AdapterSpec("local-hq", "Local HQ", None, "local", "local", "navigation"),
    AdapterSpec("hermes", "Hermes", "hermes", "local-cli", "provider-dependent", "conversation"),
    AdapterSpec("codex", "Codex", "codex", "local-cli", "provider-dependent", "implementation"),
    AdapterSpec("devin", "Devin", "devin", "cloud", "cloud", "implementation"),
    AdapterSpec("claude", "Claude", "claude", "cloud", "cloud", "implementation"),
)


def get_adapter(adapter_id: str) -> AdapterSpec | None:
    for spec in ADAPTERS:
        if spec.id == adapter_id:
            return spec
    return None


def probe(checked_at: str) -> list[dict]:
    """Report whether each adapter can be found. Missing tools are offline."""
    found: list[dict] = []
    for spec in ADAPTERS:
        if spec.command is None:
            available = True
            status = "available"
            detail = "In-process navigation only. Does not call a model."
        else:
            available = shutil.which(spec.command) is not None
            status = "available" if available else "offline"
            detail = "Executable found on PATH." if available else "Executable not found. Marked offline."
        found.append({
            "id": spec.id,
            "label": spec.label,
            "locality": spec.locality,
            "network": spec.network,
            "role": spec.role,
            "available": available,
            "status": status,
            "detail": detail,
            "probe": "path-lookup",
            "checked_at": checked_at,
        })
    return found
