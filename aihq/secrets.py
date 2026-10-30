"""Secrets for connectors and model providers. Never in the repo, never in an API response.

Lookup order for `get(name)`: environment `CURATOR_<NAME>` (dashes become underscores, upper
case), then the macOS Keychain (`security ... -s {svc} -a <name>`), then a 0600 file
`<AIHQ_HOME>/secrets/<name>`. `put` writes the Keychain on a Mac and the file elsewhere.
`AIHQ_SECRETS_BACKEND=file` forces the file backend (tests, Linux, CI).

Values are never logged or put into exception messages.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

def _service() -> str:
    from . import core
    return str(core.local_config().get("secrets_service") or "agentic-os")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")


def _check(name: str) -> str:
    name = str(name or "").strip()
    if not NAME.match(name):
        raise ValueError("Secret names are letters, digits, dashes or underscores.")
    return name


def _env_name(name: str) -> str:
    prefix = _prefix()
    return prefix + re.sub(r"[^A-Z0-9]", "_", name.upper())


def _prefix() -> str:
    from . import core
    return str(core.local_config().get("secrets_env_prefix") or "AIHQ_")


def _backend() -> str:
    forced = (os.environ.get("AIHQ_SECRETS_BACKEND") or os.environ.get("CURATOR_SECRETS_BACKEND") or "").lower()
    if forced in {"file", "keychain"}:
        return forced
    return "keychain" if sys.platform == "darwin" else "file"


def _home() -> Path:
    return Path(os.environ.get("AIHQ_HOME", str(Path.home() / "PersonalAIHQ"))).expanduser()


def _file(name: str) -> Path:
    return _home() / "secrets" / name


def _keychain(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["security", *args], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None


def get(name: str) -> str | None:
    name = _check(name)
    value = os.environ.get(_env_name(name))
    if value is None and _prefix() != "CURATOR_":
        value = os.environ.get("CURATOR_" + re.sub(r"[^A-Z0-9]", "_", name.upper()))
    if value:
        return value
    if _backend() == "keychain":
        result = _keychain("find-generic-password", "-s", _service(), "-a", name, "-w")
        if result is not None and result.returncode == 0 and result.stdout.strip():
            return result.stdout.rstrip("\n")
    path = _file(name)
    try:
        value = path.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def put(name: str, value: str) -> None:
    name = _check(name)
    value = str(value or "")
    if not value.strip():
        raise ValueError("Nothing to save: the value is empty.")
    if _backend() == "keychain":
        result = _keychain("add-generic-password", "-U", "-s", _service(), "-a", name, "-w", value)
        if result is not None and result.returncode == 0:
            return
        raise ValueError(f"The Keychain refused to save {name}.")
    path = _file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    path.chmod(0o600)


def delete(name: str) -> None:
    name = _check(name)
    if _backend() == "keychain":
        _keychain("delete-generic-password", "-s", _service(), "-a", name)
    try:
        _file(name).unlink()
    except OSError:
        pass


def present(name: str) -> bool:
    try:
        return bool(get(name))
    except ValueError:
        return False


def resolve(value):
    """`secret:<name>` strings become the secret (or "" when missing); anything else as is."""
    if isinstance(value, str) and value.startswith("secret:"):
        return get(value[7:]) or ""
    return value
