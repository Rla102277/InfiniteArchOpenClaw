import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from aihq import mcp

FAKE = str(Path(__file__).with_name("fake_mcp_server.py"))


def stdio_spec(sid="fake"):
    return {"id": sid, "transport": "stdio", "command": sys.executable, "args": [FAKE]}


class StdioTests(unittest.TestCase):
    def tearDown(self):
        mcp.close_all()

    def test_handshake_paginated_tools_and_calls(self):
        client = mcp.client(stdio_spec())
        tools = client.list_tools()
        self.assertEqual([t["name"] for t in tools], ["lookup", "post"])
        self.assertEqual(client.server_info["serverInfo"]["name"], "fake")
        self.assertEqual(client.call_tool("post", {"text": "hi"}), 'post:{"text": "hi"}')
        self.assertTrue(client.call_tool("boom", {}).startswith("ERROR: "))

    def test_pool_reuses_and_restarts_a_dead_server(self):
        first = mcp.client(stdio_spec())
        first.list_tools()
        self.assertIs(mcp.client(stdio_spec()), first)
        first.proc.kill()
        first.proc.wait()
        self.assertEqual(first.call_tool("lookup", {"q": "x"}), 'lookup:{"q": "x"}')

    def test_missing_command_is_a_clear_error(self):
        client = mcp.client({"id": "nope", "transport": "stdio", "command": "/no/such/binary"})
        with self.assertRaises(mcp.MCPError):
            client.list_tools()


class _Handler(BaseHTTPRequestHandler):
    mode = "json"

    def log_message(self, *args):
        pass

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "http-fake"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "ping", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]}
        else:
            result = {"content": [{"type": "text", "text": "pong " + self.headers.get("Authorization", "")}]}
        reply = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
        self.send_response(200)
        self.send_header("Mcp-Session-Id", "sess-1")
        if self.mode == "sse":
            body = f"event: message\ndata: {reply}\n\n".encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            body = reply.encode()
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class HTTPTests(unittest.TestCase):
    def serve(self, mode):
        handler = type(f"H{mode}", (_Handler,), {"mode": mode})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/mcp"

    def tearDown(self):
        mcp.close_all()

    def test_json_and_sse_replies_and_session(self):
        for mode in ("json", "sse"):
            client = mcp.client({"id": f"h-{mode}", "transport": "http", "url": self.serve(mode), "headers": {"Authorization": "Bearer t"}})
            self.assertEqual([t["name"] for t in client.list_tools()], ["ping"])
            self.assertEqual(client.session, "sess-1")
            self.assertEqual(client.call_tool("ping", {}), "pong Bearer t")


if __name__ == "__main__":
    unittest.main()
