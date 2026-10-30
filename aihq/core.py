"""Repo roots and the small environment state the CLI and server share.

The legacy project workspace (PROJECT.md, features, owner reviews) was removed on Oct 29 2026:
work lives in module apps under apps/<id> and in the Git brain under ../hq.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents" / "skills"
LOCAL_CONFIG = "curator.json"  # same file gitbrain reads; optional per-checkout settings


@lru_cache(maxsize=1)
def local_config() -> dict:
    """Optional per-checkout settings (owner name, extra recos, search scopes).

    Lives in curator.json at the OS root, which a generic fork does not have. Everything
    here has a generic default so the core never needs it.
    """
    path = ROOT / LOCAL_CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def owner_name() -> str:
    """Display name for the person this OS serves. Generic installs say "the owner"."""
    return (os.environ.get("AIHQ_OWNER_NAME", "").strip()
            or str(local_config().get("owner_name") or "").strip()
            or "the owner")


def owner_id() -> str:
    """Actor id for the owner in activity and chat records."""
    return re.sub(r"[^a-z0-9]+", "-", owner_name().lower()).strip("-") or "owner"


def home_display(path) -> str:
    """A path for display: $HOME shortened to ~."""
    text = str(path)
    home = str(Path.home())
    return "~" + text[len(home):] if text.startswith(home) else text


def state() -> dict:
    return {"skills": sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md")),
            "tools": {x: shutil.which(x) is not None for x in ["git", "gh", "hermes", "codex", "claude", "python3"]},
            "version": "1.1.0", "deployment": "local-development-only"}
