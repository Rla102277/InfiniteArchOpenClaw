# Agent runtime, chats and agent apps, version 1

The OS owns the model, the tool loop, the conversations and a data-defined app kind. Module apps
build on it instead of carrying their own copies.

## aihq/llm.py: the model layer

First implemented inside a module app and lifted here. Backends: `anthropic` (Messages API over urllib,
key from `ANTHROPIC_API_KEY` or the `curator-os` Keychain item), `claude-cli`, `hermes`,
`StubModel`. `pick(role_hint) -> Model`; `Model.complete(system, messages, tools, max_tokens)`.
New: `images=True` support (base64 `image` blocks inside a user message) and server tools: when
`web=True`, the anthropic backend adds `{"type": "web_search_20250305", "name": "web_search",
"max_uses": 8}` and the loop passes `server_tool_use` / `web_search_tool_result` blocks straight
back. Models that cannot search (CLI backends) get a `fetch_url` tool only, and the prompt says so.

## aihq/agentrun.py: the loop

```python
def run(model, system: str, messages: list[dict], toolbelt, max_steps=16, on_step=None) -> dict
# -> {"answer", "messages" (full transcript incl. tool blocks), "tool_calls", "usage", "stop", "seconds"}
```
`toolbelt` has `specs() -> list[dict]` and `call(name, args) -> str`. `BaseToolbelt(domain, store_like)`
provides the OS-wide tools every agent gets: `read_file`, `search_repo`, `memory`, `write_memory`,
`inbox`, `log`, `fetch_url(url)` (GET, 1 MB cap, HTML stripped to text), `records(app, filter)`,
`write_record(app, id, fields)`, `create_app(...)` (see agent apps), `list_apps`. Toolbelts
compose: `Composite([base, app_tools])`.

## aihq/chats.py: threads in the brain

```
hq/os/chats/<thread-id>.md
---
id, title, agent (operator|critic|...|app:<id>), app (optional), created, updated, messages: N
---
## the owner · 2026-10-29 00:12
text
![photo](../uploads/<sha>.jpg)      # images: stored once under hq/os/uploads/, referenced by path
## Agent · 2026-10-29 00:12
answer
<details>tool calls…</details>
```
API: `chats.list(app=None)`, `chats.get(id)`, `chats.create(title, agent, app=None)`,
`chats.append(id, role, text, images=[], tool_calls=[], usage={})`, `chats.history(id, limit_chars)`
(what the model sees: last N turns, images as blocks, older turns summarized by a one-line
"earlier in this thread" note written by the model the first time the thread exceeds the budget).
Uploads over 2 MB are downscaled to 2048 px JPEG (stdlib only: no PIL, so accept JPEG/PNG and
send to the model as is, capped at 5 MB; note the cap in the UI).

## Runtime operations and API

- `chat.send {thread?, text, agent?, app?, images?: [{name, data_base64, mime}]}` synchronous;
  creates the thread when missing; routes: `app` set -> that agent app's prompt; `agent` set ->
  that module app's agent (through the app's `chat(thread, text, agent)` if present, else the
  OS Operator); returns `{thread, message, run}`.
- `GET /api/chats?app=`, `GET /api/chats/<id>`, `POST /api/chat` (multipart or JSON with base64).
- The Overview chat and module app chat views both use these.

## Agent apps: `hq/os/apps/<id>/`

```
app.json   {"schema_version": 1, "id": "art-shows", "name": "Art Shows", "kind": "agent",
            "icon": "◎", "description": "...", "every": "1d" | "at": "06:45",
            "fields": ["title", "venue", "deadline", "fee", "url", "status", "note"],
            "key": "url", "notify": true, "web": true, "model": "default" | "deep"}
prompt.md  the standing instructions (what to look for, what counts, the voice)
```
Runner (`aihq/agentapps.py`): on schedule, runs the loop with system = prompt.md + repo rules +
memory digest + "existing records" (so it adds, updates, never duplicates by `key`), tools = Base
with `write_record` scoped to this app, web on if `web`. New or changed records produce one inbox
note "<app>: N new" with a summary, when `notify`. Records live in `hq/os/records/<id>/<key-slug>.md`
(same frontmatter format as module app boards). Chat with an app = `chat.send {app}`; the app's
prompt is the system prompt and its records are in context.

Creation: the `create_app` tool (Operator, and `./hq app create`) writes the two files and
reconciles; the shell shows it under APPS at once. Apps are data, so they travel with brain sync.
`update_app`, `delete_app` (moves to `hq/os/apps/_deleted/`), `run_app` exist too.

Shell: agent apps render generically with views Board (records table from `fields`, sort and
filter, status edit), Chat, Settings (prompt editor, cadence, fields, notify, Run now, delete).

## Rules

1. Everything persists under `hq/`; the OS private dir holds only caches.
2. Gates stay where they are: nothing here prints, writes Photos or governance.
3. Every model call is recorded with tokens; every run writes one log line.
4. No em dashes.
