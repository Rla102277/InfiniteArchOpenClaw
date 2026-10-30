"""Loopback-only HQ dashboard. No external network calls or shell execution."""
from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import core, modules

ASSETS = core.ROOT / "web"
CHAT_BODY = 8 * 1024 * 1024  # /api/chat only: a few photos as base64
MAX_BODY = 64000


def make_handler(secret: str, runtime=None):
    def local_runtime():
        nonlocal runtime
        if runtime is None:
            from .runtime import Runtime
            runtime = Runtime()
        return runtime

    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalHQ/0.1"

        def _respond(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; media-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._respond(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _safe_host(self) -> bool:
            host = self.headers.get("Host", "").split(":", 1)[0].lower()
            return host in ("127.0.0.1", "localhost")

        def do_GET(self):
            if not self._safe_host():
                return self._json(403, {"error": "Localhost only"})
            if not self._safe_origin():
                return self._json(403, {"error": "Same-origin only"})
            path = urlparse(self.path).path
            if path == "/api/os/state":
                try:
                    return self._json(200, local_runtime().snapshot())
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return self._json(400, {"error": str(exc)})
            if path == "/api/state":
                try:
                    return self._json(200, core.state())
                except (ValueError, OSError) as exc:
                    return self._json(400, {"error": str(exc)})
            if path == "/api/csrf":
                return self._json(200, {"token": secret})
            if path.startswith("/api/v1/"):
                resource = path[len("/api/v1/"):].strip("/")
                if not resource or "/" in resource:
                    return self._json(404, {"error": "Not found"})
                try:
                    from . import apiv1
                    return self._json(200, apiv1.envelope(resource, local_runtime().snapshot()))
                except ValueError as exc:
                    return self._json(404, {"error": str(exc)[:200]})
                except (OSError, sqlite3.Error) as exc:
                    return self._json(400, {"error": str(exc)[:300]})
            if path == "/api/connectors":
                try:
                    runtime = local_runtime()
                    snap = runtime._connectors_list() if hasattr(runtime, "_connectors_list") else []
                    return self._json(200, {"connectors": snap, "telegram": runtime._guarded(runtime._telegram_status, default={})})
                except Exception as exc:  # the connector layer may not be installed yet
                    return self._json(200, {"connectors": [], "error": str(exc)[:300]})
            if path == "/api/chats" or path.startswith("/api/chats/"):
                try:
                    runtime = local_runtime()
                    parts = [p for p in path.split("/") if p]
                    if len(parts) == 2:
                        query = parse_qs(urlparse(self.path).query)
                        app = (query.get("app") or [None])[0]
                        return self._json(200, runtime.action({"action": "chats.list", "app": app}))
                    if len(parts) == 3:
                        return self._json(200, runtime.action({"action": "chats.get", "id": parts[2]}))
                    return self._json(404, {"error": "Not found"})
                except (ValueError, OSError, sqlite3.Error) as exc:
                    return self._json(400, {"error": str(exc)})
            if path.startswith("/api/apps/"):
                parts = [p for p in path.split("/") if p]
                if len(parts) == 4 and parts[3] == "snapshot":
                    try:
                        runtime = local_runtime()
                        app = modules.get(runtime.brain, parts[2], runtime)
                        snap = modules.safe_call(app, "snapshot", default={"error": "no snapshot"})
                        if isinstance(snap, dict):
                            snap.setdefault("counts", {})
                        return self._json(200, snap if isinstance(snap, dict) else {"error": "snapshot must be an object"})
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        return self._json(400, {"error": str(exc)})
                return self._json(404, {"error": "Not found"})
            if path.startswith("/apps/"):
                parts = path.split("/", 3)
                if len(parts) == 4:
                    try:
                        found = modules.static(local_runtime().brain, parts[2], parts[3])
                    except (ValueError, OSError):
                        found = None
                    if found:
                        return self._respond(200, found[0], found[1])
                return self._json(404, {"error": "Not found"})
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
                      "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
                      "/agent-app.js": ("agent-app.js", "text/javascript; charset=utf-8"),
                      "/agent-app.css": ("agent-app.css", "text/css; charset=utf-8"),
                      "/command": ("command.html", "text/html; charset=utf-8"),
                      "/command.js": ("command.js", "text/javascript; charset=utf-8"),
                      "/command.css": ("command.css", "text/css; charset=utf-8"),
                      "/fonts/rajdhani-600.woff2": ("fonts/rajdhani-600.woff2", "font/woff2"),
                      "/fonts/ibm-plex-mono-400.woff2": ("fonts/ibm-plex-mono-400.woff2", "font/woff2"),
                      "/icon-192.png": ("icon-192.png", "image/png"),
                      "/icon-512.png": ("icon-512.png", "image/png"),
                      "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png")}
            if path not in assets:
                return self._json(404, {"error": "Not found"})
            filename, mime = assets[path]
            if not (ASSETS / filename).is_file():
                return self._json(404, {"error": "Not found"})
            return self._respond(200, (ASSETS / filename).read_bytes(), mime)

        def _safe_origin(self):
            expected = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return (origin is None or origin in expected) and self.headers.get("Sec-Fetch-Site", "none") != "cross-site"

        def do_POST(self):
            if not self._safe_host():
                return self._json(403, {"error": "Localhost only"})
            # Same-origin check plus per-process CSRF token. Browser never talks beyond loopback.
            if not self._safe_origin():
                return self._json(403, {"error": "Invalid origin"})
            if not hmac.compare_digest(self.headers.get("X-HQ-Token", "").encode("utf-8"), secret.encode("utf-8")):
                return self._json(403, {"error": "Invalid session token"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request_path = urlparse(self.path).path
                cap = CHAT_BODY if request_path == "/api/chat" else MAX_BODY
                if not 0 < length <= cap:
                    raise ValueError("Invalid request size")
                if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                    raise ValueError("Expected JSON")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Expected object")
                path = urlparse(self.path).path
                if path == "/api/os/action":
                    if payload.get("action") == "task.recover":
                        raise ValueError("Recover interrupted tasks from the local CLI only.")
                    result = local_runtime().action(payload)
                elif path == "/api/chat":
                    result = local_runtime().action({"action": "chat.send", "thread": payload.get("thread"), "text": payload.get("text"),
                                                     "agent": payload.get("agent"), "app": payload.get("app"),
                                                     "model": payload.get("model"), "images": payload.get("images") or []})
                elif path == "/api/secrets":
                    result = _secret_op(payload, local_runtime())
                elif path == "/api/connectors":
                    result = _connector_op(local_runtime(), payload)
                elif path.startswith("/api/apps/"):
                    parts = [p for p in path.split("/") if p]
                    if len(parts) != 4 or parts[3] not in {"action", "ask"}:
                        return self._json(404, {"error": "Not found"})
                    if parts[3] == "action":
                        result = local_runtime().action({"action": "module.action", "app": parts[2], "payload": payload})
                    else:
                        result = local_runtime().action({"action": "module.ask", "app": parts[2],
                                                         "text": payload.get("text"), "agent": payload.get("agent")})
                else:
                    return self._json(404, {"error": "Not found"})
                self._json(201, {"ok": True, "result": result})
            except (ValueError, TypeError, OSError, RecursionError, sqlite3.Error, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, fmt, *args):
            # Avoid printing potentially personal project input into the terminal log.
            pass

    return Handler


SECRET_NAME = __import__("re").compile(r"[a-z][A-Za-z0-9_.-]{0,80}")


def _secret_op(payload: dict, runtime=None) -> dict:
    """Store (or delete) a secret. Never returns a value: only whether one is present."""
    from . import secrets as os_secrets
    name = str(payload.get("name") or "").strip()
    if not SECRET_NAME.fullmatch(name):
        raise ValueError("Secret names are lowercase, like openai, gmail-app-password or mcp-notion-NOTION_TOKEN.")
    if payload.get("delete"):
        os_secrets.delete(name)
    else:
        value = str(payload.get("value") or "")
        if not value.strip():
            raise ValueError("Paste the value to save.")
        os_secrets.put(name, value.strip())
    present = bool(os_secrets.present(name))
    if runtime is not None:
        runtime.__dict__.get("_section_cache", {}).clear()  # the pickers show the new key at once
    if name == "telegram-bot-token" and present:
        try:
            from .runtime import Runtime  # the gateway starts as soon as the token exists
            (runtime or Runtime()).start_gateway()
        except Exception:
            pass
    return {"ok": True, "name": name, "present": present}


def _connector_op(runtime, payload: dict) -> dict:
    from . import connectors as conn
    op = payload.get("op")
    domain = runtime.brain.root.parent
    runtime.__dict__.get("_section_cache", {}).clear()
    if op == "add_mcp":
        spec = payload.get("spec") or {k: v for k, v in payload.items() if k != "op"}
        return {"ok": True, "connector": conn.add_mcp(domain, spec)}
    if op == "remove_mcp":
        return {"ok": True, "removed": conn.remove_mcp(domain, str(payload.get("id") or ""))}
    if op == "update":
        return {"ok": True, "connector": conn.update(domain, str(payload.get("id") or ""), payload.get("changes") or {})}
    if op == "test":
        connector = conn.registry(domain).get(str(payload.get("id") or ""))
        if connector is None:
            raise ValueError("No such connector.")
        status = connector.status()
        tools = connector.tools() if status.get("ok") else []
        return {"ok": bool(status.get("ok")), "status": status, "tools": [t.get("name") for t in tools]}
    if op == "telegram_allow":
        from . import telegram_gateway as tg
        ids = [str(x).strip() for x in payload.get("allow") or [] if str(x).strip().isdigit()]
        return {"ok": True, "telegram": tg.save_config(domain, {"allow": ids})}
    raise ValueError("op is add_mcp, update, remove_mcp, test or telegram_allow.")


def _worker_loop(runtime, stop: threading.Event, allow_external: bool) -> None:
    """The dashboard's own worker. Agent tasks still run only after you approve them."""
    while not stop.wait(5):
        try:
            runtime.tick(allow_external=allow_external)
        except Exception:
            # Drift (reconcile first) or one bad task must not stop the dashboard.
            continue


def serve(host: str = "127.0.0.1", port: int = 8731, open_browser: bool = True, worker: bool = True) -> None:
    if host != "127.0.0.1":
        raise ValueError("HQ starter only supports loopback binding")
    secret = secrets.token_urlsafe(32)
    from .runtime import Runtime
    runtime = Runtime()
    # Reconcile at every start: a pulled commit may carry a new app or routine, and the worker
    # refuses to run while declarations drift. The dashboard's Reconcile button stays for mid-run.
    try:
        runtime.reconcile()
    except ValueError as exc:
        print(f"Declarations did not validate: {exc}. Fix them, or use the dashboard's Reconcile after.")
    server = ThreadingHTTPServer((host, port), make_handler(secret, runtime))
    stop = threading.Event()
    if worker:
        worker_runtime = Runtime()
        threading.Thread(target=_worker_loop, args=(worker_runtime, stop, True), daemon=True).start()
        if worker_runtime.start_gateway():
            print("Telegram: connected. Message your bot from your phone.")
    url = f"http://{host}:{server.server_port}"
    print(f"Personal AI HQ is running at {url}")
    print("Worker running: approved agent tasks and due schedules run every few seconds." if worker
          else "Worker off (--no-worker). Start ./hq worker separately for scheduled jobs.")
    print("Press Control-C to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
