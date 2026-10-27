"""Loopback-only HQ dashboard. No external network calls or shell execution."""
from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import core

ASSETS = core.ROOT / "web"
MAX_BODY = 16000


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
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
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
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path not in assets:
                return self._json(404, {"error": "Not found"})
            filename, mime = assets[path]
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
                if not 0 < length <= MAX_BODY:
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
                elif path == "/api/projects":
                    result = core.create_project(payload.get("name"), payload.get("idea"))
                elif path == "/api/features":
                    result = core.create_feature(payload.get("project"), payload.get("title"), payload.get("description"))
                elif path == "/api/decide":
                    result = core.decide_review(payload.get("project"), payload.get("review_id"), payload.get("decision"))
                else:
                    return self._json(404, {"error": "Not found"})
                self._json(201, {"ok": True, "result": result})
            except (ValueError, TypeError, OSError, RecursionError, sqlite3.Error, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def log_message(self, fmt, *args):
            # Avoid printing potentially personal project input into the terminal log.
            pass

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8731, open_browser: bool = True) -> None:
    if host != "127.0.0.1":
        raise ValueError("HQ starter only supports loopback binding")
    secret = secrets.token_urlsafe(32)
    from .runtime import Runtime
    runtime = Runtime()
    with runtime.connect() as connection:
        initial = runtime.meta(connection, "brain_fingerprint") is None
    if initial:
        runtime.reconcile()
    server = ThreadingHTTPServer((host, port), make_handler(secret, runtime))
    url = f"http://{host}:{server.server_port}"
    print(f"Personal AI HQ is running at {url}")
    print("Local-only dashboard. Start ./hq worker separately for scheduled jobs.")
    print("Press Control-C to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
