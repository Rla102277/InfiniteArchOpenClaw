"""A small MCP client: JSON-RPC 2.0 over stdio or streamable HTTP. Stdlib only.

`client(spec)` returns a live client for a server spec from hq/os/connectors.json, from a
module-level pool keyed by server id (a stdio server process is started on first use, kept for
the life of the OS process, restarted when it died). `close_all()` stops everything.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
CLIENT = {"name": "infinitearch-agentic-os", "version": "1.0"}
TIMEOUT = 60


class MCPError(Exception):
    pass


def _resolve(value):
    from . import secrets
    return secrets.resolve(value)


class _Base:
    def __init__(self, spec: dict):
        self.spec = spec
        self.id = str(spec.get("id", "server"))
        self._next = 0
        self._lock = threading.Lock()
        self.server_info: dict = {}
        self.initialized = False

    def _id(self) -> int:
        self._next += 1
        return self._next

    def request(self, method: str, params: dict | None = None, timeout: float = TIMEOUT):
        raise NotImplementedError

    def notify(self, method: str, params: dict | None = None):
        raise NotImplementedError

    def ensure(self):
        if self.initialized:
            return
        result = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT})
        self.server_info = result if isinstance(result, dict) else {}
        self.notify("notifications/initialized")
        self.initialized = True

    def list_tools(self) -> list[dict]:
        self.ensure()
        tools, cursor = [], None
        for _ in range(50):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> str:
        self.ensure()
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        parts = []
        for block in result.get("content") or []:
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text", "")))
            elif kind == "image":
                parts.append("[image]")
            elif kind in ("resource", "resource_link"):
                uri = (block.get("resource") or {}).get("uri") or block.get("uri") or ""
                parts.append(f"[resource {uri}]")
            else:
                parts.append(f"[{kind}]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        text = "\n".join(parts) if parts else "(no content)"
        return ("ERROR: " + text) if result.get("isError") else text

    def close(self):
        pass


class StdioClient(_Base):
    def __init__(self, spec: dict):
        super().__init__(spec)
        self.proc: subprocess.Popen | None = None
        self.pending: dict[int, queue.Queue] = {}
        self.reader: threading.Thread | None = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _close_pipes(self):
        if self.proc is None:
            return
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except (OSError, ValueError):
                pass

    def start(self):
        self._close_pipes()  # a dead server's pipes, before a new process takes its place
        command = self.spec.get("command")
        if not command:
            raise MCPError(f"{self.id}: no command to start")
        env = dict(os.environ)
        for key, value in (self.spec.get("env") or {}).items():
            env[str(key)] = str(_resolve(value))
        try:
            self.proc = subprocess.Popen([command, *[str(a) for a in self.spec.get("args") or []]],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=env, text=True, bufsize=1, cwd=self.spec.get("cwd") or None)
        except OSError as exc:
            raise MCPError(f"{self.id}: could not start {command}: {exc}") from exc
        self.initialized = False
        self.pending = {}
        self.reader = threading.Thread(target=self._read, daemon=True, name=f"mcp-{self.id}")
        self.reader.start()

    def _read(self):
        proc = self.proc
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and "id" in message and ("result" in message or "error" in message):
                waiter = self.pending.pop(message["id"], None)
                if waiter is not None:
                    waiter.put(message)
        for waiter in list(self.pending.values()):
            waiter.put({"error": {"message": "server exited"}})

    def _send(self, message: dict):
        if not self.alive():
            self.start()
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")  # type: ignore[union-attr]
            self.proc.stdin.flush()  # type: ignore[union-attr]
        except (OSError, ValueError) as exc:
            raise MCPError(f"{self.id}: write failed: {exc}") from exc

    def request(self, method, params=None, timeout=TIMEOUT):
        if not self.alive():
            self.start()  # a dead or never-started server: start it and handshake first
            if method != "initialize":
                self.ensure()
        with self._lock:
            rid = self._id()
            waiter: queue.Queue = queue.Queue()
            self.pending[rid] = waiter
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            message = waiter.get(timeout=timeout)
        except queue.Empty as exc:
            self.pending.pop(rid, None)
            raise MCPError(f"{self.id}: {method} timed out after {timeout:.0f}s") from exc
        if "error" in message:
            raise MCPError(f"{self.id}: {method}: {message['error'].get('message', message['error'])}")
        return message.get("result")

    def notify(self, method, params=None):
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdin.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                    self.proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if self.reader is not None:
                self.reader.join(timeout=2)
            self._close_pipes()
        self.proc = None
        self.initialized = False


class HTTPClient(_Base):
    def __init__(self, spec: dict, opener=None):
        super().__init__(spec)
        self.url = str(spec.get("url") or "")
        if not self.url.startswith(("http://", "https://")):
            raise MCPError(f"{self.id}: url must be http(s)")
        self.session: str | None = None
        self.opener = opener or urllib.request.urlopen

    def _post(self, message: dict, timeout: float):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL}
        for key, value in (self.spec.get("headers") or {}).items():
            headers[str(key)] = str(_resolve(value))
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(message).encode("utf-8"), headers=headers, method="POST")
        try:
            response = self.opener(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            raise MCPError(f"{self.id}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise MCPError(f"{self.id}: {exc}") from exc
        with response:
            session = response.headers.get("Mcp-Session-Id")
            if session:
                self.session = session
            kind = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            raw = response.read(4_000_000).decode("utf-8", errors="replace")
        if not raw.strip():
            return None
        if kind == "text/event-stream":
            found = None
            for event in raw.split("\n\n"):
                data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                if not data:
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("id") == message.get("id"):
                    found = payload
            return found
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MCPError(f"{self.id}: the server did not answer JSON") from exc

    def request(self, method, params=None, timeout=TIMEOUT):
        with self._lock:
            rid = self._id()
        message = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, timeout)
        if message is None:
            raise MCPError(f"{self.id}: {method}: empty reply")
        if isinstance(message, list):
            message = next((m for m in message if m.get("id") == rid), message[0] if message else {})
        if "error" in message:
            raise MCPError(f"{self.id}: {method}: {message['error'].get('message', message['error'])}")
        return message.get("result")

    def notify(self, method, params=None):
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, 20)
        except MCPError:
            pass


_pool: dict[str, _Base] = {}
_pool_lock = threading.Lock()


def client(spec: dict) -> _Base:
    sid = str(spec.get("id", "server"))
    transport = spec.get("transport", "stdio")
    with _pool_lock:
        existing = _pool.get(sid)
        if existing is not None and existing.spec == spec:
            return existing
        if existing is not None:
            existing.close()
        made = HTTPClient(spec) if transport == "http" else StdioClient(spec)
        _pool[sid] = made
        return made


def close_all():
    with _pool_lock:
        for item in _pool.values():
            try:
                item.close()
            except Exception:
                pass
        _pool.clear()
