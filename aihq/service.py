"""Start the OS at login on a Mac: a per-user launchd LaunchAgent, nothing system-wide.

`./hq install-service` writes ~/Library/LaunchAgents/com.theinfinitearch.agentic-os.plist, which
runs `python3 -m aihq.cli start --no-browser` from this checkout, restarts it if it dies, and
logs to ~/Library/Logs/agentic-os.log. `--darkroom` adds a second agent that opens IA Darkroom
with its control port at login, so the Darkroom app in the OS is live from the start.

launchd gives jobs a bare PATH, so the plist carries the places Claude Code, Hermes, cargo and
Homebrew put their binaries. The login keychain is unlocked for LaunchAgents, so API keys stored there resolve. Nothing here needs sudo.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

OS_LABEL = "com.theinfinitearch.agentic-os"
DARKROOM_LABEL = "com.theinfinitearch.darkroom-app"
AGENTS = Path.home() / "Library" / "LaunchAgents"
LOGS = Path.home() / "Library" / "Logs"


def _path_env() -> str:
    home = Path.home()
    extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", str(home / ".local" / "bin"), str(home / ".cargo" / "bin"),
             "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    seen, out = set(), []
    for item in extra + os.environ.get("PATH", "").split(":"):
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return ":".join(out)


def os_plist(os_root: Path, port: int | None = None) -> dict:
    python = sys.executable or shutil.which("python3") or "/usr/bin/python3"
    args = [python, "-m", "aihq.cli", "start", "--no-browser"]
    if port:
        args += ["--port", str(port)]
    env = {"PATH": _path_env(), "HOME": str(Path.home()), "LANG": "en_US.UTF-8"}
    for key in ("AIHQ_HOME", "ANTHROPIC_API_KEY"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return {
        "Label": OS_LABEL,
        "ProgramArguments": args,
        "WorkingDirectory": str(os_root),
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(LOGS / "agentic-os.log"),
        "StandardErrorPath": str(LOGS / "agentic-os.log"),
        "ProcessType": "Interactive",
    }


def darkroom_plist(control_port: int = 7980) -> dict:
    return {
        "Label": DARKROOM_LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", "IA Darkroom", "--args", "--control", str(control_port)],
        "RunAtLoad": True,
        "StandardOutPath": str(LOGS / "darkroom-app.log"),
        "StandardErrorPath": str(LOGS / "darkroom-app.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=30)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _write(label: str, data: dict) -> Path:
    AGENTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    path = AGENTS / f"{label}.plist"
    with path.open("wb") as handle:
        plistlib.dump(data, handle)
    return path


def _load(path: Path, label: str) -> str:
    _launchctl("bootout", _domain(), str(path))  # replace a previous version quietly
    result = _launchctl("bootstrap", _domain(), str(path))
    if result.returncode != 0:
        return f"written {path}, but launchctl could not load it: {(result.stderr or result.stdout).strip()}"
    _launchctl("kickstart", "-k", f"{_domain()}/{label}")
    return f"installed and started: {path}"


def install(os_root: Path, port: int | None = None, darkroom: bool = False) -> list[str]:
    if sys.platform != "darwin":
        raise ValueError("install-service writes a macOS LaunchAgent; on this system start the OS with ./hq start.")
    lines = [_load(_write(OS_LABEL, os_plist(os_root, port)), OS_LABEL)]
    if darkroom:
        lines.append(_load(_write(DARKROOM_LABEL, darkroom_plist()), DARKROOM_LABEL))
    lines.append(f"log: {LOGS / 'agentic-os.log'}")
    lines.append("The OS now starts at login and restarts if it stops. Dashboard: http://127.0.0.1:%s" % (port or 8731))
    return lines


def uninstall() -> list[str]:
    lines = []
    for label in (OS_LABEL, DARKROOM_LABEL):
        path = AGENTS / f"{label}.plist"
        if not path.exists():
            continue
        _launchctl("bootout", _domain(), str(path))
        path.unlink()
        lines.append(f"removed {path}")
    return lines or ["nothing installed"]


def status() -> list[str]:
    lines = []
    for label in (OS_LABEL, DARKROOM_LABEL):
        path = AGENTS / f"{label}.plist"
        if not path.exists():
            lines.append(f"{label}: not installed")
            continue
        result = _launchctl("print", f"{_domain()}/{label}")
        state = "loaded" if result.returncode == 0 else "installed but not loaded"
        pid = ""
        for line in result.stdout.splitlines():
            if line.strip().startswith("pid = "):
                pid = " (" + line.strip() + ")"
        lines.append(f"{label}: {state}{pid}")
    return lines
