"""Loopback-only HQ dashboard. No external network calls or shell execution from chat."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, core, hq

ASSETS = core.ROOT / "web"
MAX_BODY = 16000
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/command": ("command.html", "text/html; charset=utf-8"),
    "/command.js": ("command.js", "text/javascript; charset=utf-8"),
    "/command.css": ("command.css", "text/css; charset=utf-8"),
    "/themes/themes.css": ("themes/themes.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
}
COMMAND_PATHS = {"/command", "/command.js", "/command.css", "/sw.js"}


def make_handler(secret: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PersonalHQ/0.4"

        def _respond(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; worker-src 'self'; manifest-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict) -> None:
            self._respond(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _safe_host(self) -> bool:
            host = self.headers.get("Host", "").split(":", 1)[0].lower()
            return host in ("127.0.0.1", "localhost")

        def _csrf_ok(self) -> bool:
            origin = self.headers.get("Origin", "")
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if origin and origin not in (expected, f"http://localhost:{self.server.server_port}"):
                self._json(403, {"error": "Invalid origin"})
                return False
            if not hmac.compare_digest(self.headers.get("X-HQ-Token", ""), secret):
                self._json(403, {"error": "Invalid session token"})
                return False
            return True

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ValueError("Invalid request size")
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError("Expected JSON")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected object")
            return payload

        def _serve_static(self, path: str) -> None:
            if path in COMMAND_PATHS and not api.command_center_enabled():
                self._json(404, {"error": "Command center is turned off"})
                return
            if path not in STATIC:
                self._json(404, {"error": "Not found"})
                return
            filename, mime = STATIC[path]
            file_path = (ASSETS / filename).resolve()
            if not file_path.is_relative_to(ASSETS.resolve()) or not file_path.is_file():
                self._json(404, {"error": "Not found"})
                return
            self._respond(200, file_path.read_bytes(), mime)

        def _dispatch(self, method: str) -> None:
            if not self._safe_host():
                self._json(403, {"error": "Localhost only"})
                return
            if method != "GET" and not self._csrf_ok():
                return
            parsed = urlparse(self.path)
            body = None
            if method != "GET":
                try:
                    body = self._read_body()
                except (ValueError, json.JSONDecodeError) as exc:
                    self._json(400, {"error": str(exc)})
                    return
            try:
                status, payload = api.dispatch(method, parsed.path, parse_qs(parsed.query), body)
            except Exception:
                self._json(500, {"ok": False, "error": "The local headquarters could not complete that request."})
                return
            self._json(status, payload)

        def do_GET(self):
            if not self._safe_host():
                return self._json(403, {"error": "Localhost only"})
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._json(200, core.state())
            if path == "/api/csrf":
                return self._json(200, {"token": secret})
            if path.startswith("/api/v1/"):
                return self._dispatch("GET")
            self._serve_static(path)

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/api/v1/"):
                return self._dispatch("POST")
            if not self._safe_host():
                return self._json(403, {"error": "Localhost only"})
            if not self._csrf_ok():
                return
            try:
                payload = self._read_body()
                if path == "/api/projects":
                    result = core.create_project(payload.get("name"), payload.get("idea"))
                elif path == "/api/features":
                    result = core.create_feature(payload.get("project"), payload.get("title"), payload.get("description"))
                elif path == "/api/decide":
                    result = core.decide_review(payload.get("project"), payload.get("review_id"), payload.get("decision"))
                else:
                    return self._json(404, {"error": "Not found"})
                self._json(201, {"ok": True, "result": result})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})

        def do_PUT(self):
            self._dispatch("PUT")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def log_message(self, fmt, *args):
            # Avoid printing potentially personal project input into the terminal log.
            pass

    return Handler


def _tick_loop(stop: threading.Event) -> None:
    while not stop.wait(30):
        try:
            hq.tick()
        except Exception:
            # A failed local tick must not stop the dashboard.
            continue


def serve(host: str = "127.0.0.1", port: int = 8731, open_browser: bool = True) -> None:
    if host != "127.0.0.1":
        raise ValueError("HQ starter only supports loopback binding")
    secret = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((host, port), make_handler(secret))
    stop = threading.Event()
    ticker = threading.Thread(target=_tick_loop, args=(stop,), daemon=True)
    ticker.start()
    url = f"http://{host}:{server.server_port}"
    print(f"Personal AI HQ is running at {url}")
    print(f"Infinite Arch headquarters: {url}/command")
    print("Local-only: no remote network access or background AI execution is enabled.")
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
