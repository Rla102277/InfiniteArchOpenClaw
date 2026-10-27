"""Explicit, replaceable local process adapters; no dependency installation."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from .brain import digest, text

IDS = {"hermes", "codex", "claude", "devin", "ollama"}


def statuses():
    result = []
    for name in ["hermes", "codex", "claude"]:
        available = shutil.which(name) is not None
        result.append({"id": name, "status": "installed-unverified" if available else "missing",
                       "detail": "Requires provider setup and an approved CLI worker run; not a sandbox."})
    result.extend([
        {"id": "devin", "status": "handoff-only", "detail": "Portable task packet export; no cloud submission or polling."},
        {"id": "ollama", "status": "requires-local-server", "detail": "HTTP to 127.0.0.1:11434 only. Model must already be downloaded."},
    ])
    return result


def command(adapter, prompt):
    if adapter == "hermes":
        return ["hermes", "chat", "--oneshot", "--max-turns", "8", "-q", prompt]
    if adapter == "codex":
        return ["codex", "exec", "--sandbox", "read-only", "--color", "never", "--", prompt]
    if adapter == "claude":
        return ["claude", "-p", "--permission-mode", "plan", "--tools", "", "--", prompt]
    raise ValueError("This adapter does not use a local CLI.")


def context(payload, brain):
    adapter = payload["adapter"]
    executable = shutil.which(adapter) if adapter in {"hermes", "codex", "claude"} else None
    signature = None
    if executable:
        signature = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
    return {"payload": payload, "brain": brain.fingerprint(), "head": brain.revision()["head"],
            "executable": executable, "binary": signature}


def binding(payload, brain):
    return digest(context(payload, brain))


def execute(payload, root, timeout=120, expected_binding=None, brain=None):
    adapter = payload["adapter"]
    prompt = text(payload["prompt"])
    verified = None
    if expected_binding is not None:
        if brain is None:
            raise ValueError("Approved execution requires a brain context.")
        verified = context(payload, brain)
        if digest(verified) != expected_binding:
            raise ValueError("Approval context changed before launch; submit a new request.")
    if adapter == "devin":
        raise ValueError("Devin is handoff-only. Use ./hq handoff to produce a portable task packet.")
    if adapter == "ollama":
        connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=timeout)
        try:
            body = {"model": text(payload.get("model", "qwen3:8b"), 120),
                    "messages": [{"role": "user", "content": prompt}], "stream": False, "max_tokens": 1024}
            connection.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read(1048577)
            if response.status != 200 or len(data) > 1048576:
                raise ValueError(f"Local model failed (HTTP {response.status}) or returned too much data.")
            return text(json.loads(data)["choices"][0]["message"]["content"], 64000)
        finally:
            connection.close()
    args = command(adapter, prompt)
    executable = verified["executable"] if verified else shutil.which(args[0])
    if not executable:
        raise ValueError(f"{adapter} is missing. Install and configure it using the official guide.")
    args[0] = executable
    if verified and hashlib.sha256(Path(executable).read_bytes()).hexdigest() != verified["binary"]:
        raise ValueError("Approved executable changed before launch.")
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(args, cwd=root, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                process.terminate()
            grace_expired = False
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                grace_expired = True
                if os.name != "posix":
                    process.kill()
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if grace_expired:
                process.wait()
            raise
        output.seek(0)
        result = output.read(64000).decode("utf-8", errors="replace")
    if code != 0:
        raise ValueError(f"{adapter} exited with {code}. Local output: {result[:4000]}")
    return result or "Agent completed without text output."
