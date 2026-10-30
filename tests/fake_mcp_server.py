"""A tiny stdio MCP server for tests: two tools, one read-only, both echo."""
import json
import sys

TOOLS = [
    {"name": "lookup", "description": "Read something.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "post", "description": "Do something outside.", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
]


def reply(rid, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": rid}
    if error:
        message["error"] = {"code": -32000, "message": error}
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, rid = msg.get("method"), msg.get("id")
    if rid is None:
        continue  # notifications
    if method == "initialize":
        reply(rid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}})
    elif method == "tools/list":
        if not msg.get("params", {}).get("cursor"):
            reply(rid, {"tools": TOOLS[:1], "nextCursor": "page2"})
        else:
            reply(rid, {"tools": TOOLS[1:]})
    elif method == "tools/call":
        params = msg.get("params", {})
        if params.get("name") == "boom":
            reply(rid, {"content": [{"type": "text", "text": "it broke"}], "isError": True})
        else:
            reply(rid, {"content": [{"type": "text", "text": f"{params.get('name')}:{json.dumps(params.get('arguments'), sort_keys=True)}"}]})
    else:
        reply(rid, error=f"unknown method {method}")
