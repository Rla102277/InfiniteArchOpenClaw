# Connectors, bots, models and activity, version 1

What turns the OS from a dashboard into something that does things: agents reach the outside
world through **connectors**, run as **bots** with triggers and a per-action policy, use **any
model** (like T3 Chat), and every outside action shows up in **Activity**.

Stdlib Python only. Secrets never enter the repo. No em dashes.

## 1. Secrets: `aihq/secrets.py`

```python
def get(name: str) -> str | None        # env CURATOR_<NAME> first, then macOS Keychain
                                        # (security find-generic-password -s curator-os -a <name> -w),
                                        # then <AIHQ_HOME>/secrets/<name> (0600) on non-mac
def put(name: str, value: str) -> None  # Keychain on mac (-U to update), else the 0600 file
def delete(name: str) -> None
def present(name: str) -> bool
```
Names are slugs: `anthropic`, `openai`, `gemini`, `openrouter`, `gmail-app-password`,
`telegram-bot-token`, `instagram-token`, `mcp-<server>-<VAR>`. The server's POST
`/api/secrets {name, value}` calls `put`; no API ever returns a secret value, only `present`.

## 2. Connectors: `aihq/connectors.py`

A connector exposes tools. Tool names are namespaced `<connector>.<tool>`, e.g.
`gmail.search`, `calendar.create_event`, `telegram.send`, `mcp.notion.search_pages`.

```python
class Connector:
    id: str; name: str; kind: "builtin" | "mcp"; description: str
    def status(self) -> dict      # {"ok": bool, "detail": str, "needs": [secret names missing]}
    def tools(self) -> list[dict] # Anthropic tool specs, names WITHOUT the namespace
    def call(self, tool: str, args: dict) -> str   # returns text (JSON string ok)
    ACTIONS: set[str]             # tool names that act on the outside world (send, create, post).
                                  # Everything else is a read. Policy applies to ACTIONS only.

def registry(domain) -> dict[str, Connector]   # builtins + MCP servers from hq/os/connectors.json
class ConnectorTools:   # a Toolbelt over the connectors a bot or chat is allowed to use
    def __init__(self, domain, allowed: list[str] | "all", policy: dict[str, "auto"|"ask"], who: str, runtime=None)
    def specs(self) -> list[dict]          # namespaced
    def call(self, name, args) -> str      # reads run; ACTIONS with policy "ask" create an approval
                                           # (inbox kind approval, action connector.call, args) and
                                           # return "QUEUED for the owner's approval: <id>"; "auto" runs
                                           # and records Activity. Unknown policy defaults to "ask".
def execute_approved(domain, item) -> dict # runs a queued connector.call once, records Activity
```
`hq/os/connectors.json` (in the repo, no secrets):
```json
{"mcp": [{"id": "notion", "name": "Notion", "transport": "stdio", "command": "npx",
          "args": ["-y", "@notionhq/notion-mcp-server"], "env": {"NOTION_TOKEN": "secret:mcp-notion-NOTION_TOKEN"},
          "enabled": true},
         {"id": "remote-x", "transport": "http", "url": "https://.../mcp", "headers": {"Authorization": "secret:mcp-remote-x-token"}}],
 "builtin": {"gmail": {"address": "rla1022@gmail.com"}, "telegram": {"chat_id": "123"}}}
```
`secret:<name>` values are resolved through `secrets.get` at call time.

### MCP client: `aihq/mcp.py`
JSON-RPC 2.0, protocol version "2025-06-18" (accept the server's answer). Transports: stdio
(newline-delimited JSON over a subprocess; a server process is started on first use, kept alive
per OS process, restarted if it died) and streamable HTTP (POST JSON, accept `application/json`
or a `text/event-stream` reply; carry `Mcp-Session-Id`). `initialize` -> `notifications/initialized`
-> `tools/list` (paginate `nextCursor`) -> `tools/call` (concatenate `text` content; non-text
content summarized as `[image]` / `[resource uri]`; `isError` -> prefix "ERROR: "). Timeouts 60 s
per call. An MCP connector's ACTIONS: every tool whose `annotations.readOnlyHint` is not true.

### Built-in connectors: `aihq/builtins/`
- `gmail.py` (IMAP/SMTP to imap.gmail.com/smtp.gmail.com with the address from connectors.json
  and secret `gmail-app-password`; Google "App passwords" need 2-step verification).
  Tools: `search(query, limit=20)` (Gmail X-GM-RAW search; returns from, subject, date, uid,
  snippet), `read(uid)`, `draft(to, subject, body, reply_to_uid?)` (IMAP APPEND to
  `[Gmail]/Drafts`), `send(to, subject, body, reply_to_uid?)`. ACTIONS: draft, send.
- `calendar.py`: macOS Calendar.app through `osascript` (works for whatever accounts Calendar
  has, Google included). Tools: `list_calendars()`, `events(start, end, calendar?)`,
  `create_event(title, start, end?, all_day?, calendar?, notes?, url?)` (dedupe: an event with
  the same title on the same day is not created twice), `delete_event` is NOT offered. Off the
  Mac or if Calendar automation is refused: `create_event` writes an `.ics` into
  hq/os/calendar/<slug>.ics and says so. Config `calendar: {"name": "Infinite Arch"}` default
  calendar. ACTIONS: create_event.
- `telegram.py`: Bot API over HTTPS (`telegram-bot-token`; `chat_id` from connectors.json,
  learned on the first /start from an allowlisted user and saved there). Tools: `send(text)`,
  `send_photo(path, caption)` (paths under hq/ only). ACTIONS: send, send_photo. Plus the
  gateway (see 5).
- `instagram.py`: drafts only in v1. `draft_post(caption, image_path)` writes a record to the
  `posts` board (hq/os/records/posts/) with status draft; publishing needs the Graph API and a
  public image URL, so `publish` returns "not wired yet: copy the caption, post from your phone"
  plus sends the draft to Telegram if configured. `site.py`: `draft_update(title, body)` writes
  a markdown draft to hq/os/drafts/site/ for the site's own pipeline. ACTIONS: none that leave
  the machine in v1 (both are drafts), but mark them ACTIONS so policy is honored uniformly.
- `web.py` already exists in spirit (fetch_url, web search via model); not a connector.

## 3. Models like T3 Chat: `aihq/llm.py` additions

`OpenAICompatModel(name, base_url, key_name)` speaking `/chat/completions` with tools: converts
the Anthropic-shaped messages and tool specs to OpenAI format and the reply back
(`tool_calls` -> `tool_use` blocks, `tool` role messages from `tool_result` blocks, images as
`image_url` data URLs). Providers:
| id | base_url | secret |
|---|---|---|
| openai | https://api.openai.com/v1 | openai |
| gemini | https://generativelanguage.googleapis.com/v1beta/openai | gemini |
| openrouter | https://openrouter.ai/api/v1 | openrouter |
| ollama | http://127.0.0.1:11434/v1 | none |
Model refs are `provider:model`, e.g. `anthropic:claude-sonnet-5-5`, `openai:gpt-5`,
`gemini:gemini-2.5-pro`, `openrouter:<any>`, `ollama:qwen3:8b`. `llm.resolve(ref) -> Model`.
`llm.catalog() -> [{"ref","provider","label","configured": bool}]` (a short built-in list plus
whatever the user typed before, saved in `hq/os/models.json`). Web search is native only on
Anthropic; other providers get `fetch_url` and the prompt says so. Default stays
`anthropic:<default>` when its key exists.

## 4. Bots: agent apps grow triggers, connectors, policy and a model

`hq/os/apps/<id>/app.json` gains optional keys (old files stay valid):
```json
"triggers": [{"type": "schedule", "every": "1d"} | {"type": "schedule", "at": "07:00"},
             {"type": "email", "query": "from:(entries OR submittable) newer_than:2d", "every": "30m"},
             {"type": "telegram", "command": "/scout"}],
"connectors": ["calendar", "telegram"] | "all",
"policy": {"calendar.create_event": "auto", "telegram.send": "auto", "gmail.send": "ask", "*": "ask"},
"model": "anthropic:claude-sonnet-5-5"
```
`every`/`at` at the top level keep working as a schedule trigger. An email trigger polls Gmail
with its query and runs the bot with the new messages (uids remembered in
hq/os/apps/<id>/state.json). A telegram trigger runs the bot when the owner sends the command.
The runner composes `RecordTools + BaseToolbelt + ConnectorTools(allowed, policy, who="app:<id>")`.
Policy lookup: exact tool name, then `<connector>.*`, then `*`, default "ask". Printing,
Photos writes and governance writes are never "auto" whatever the file says.

## 5. Telegram gateway: `aihq/telegram_gateway.py`

Runs inside the OS worker thread when the token is present: long-poll `getUpdates` (timeout
25 s, offset persisted in the private dir). Only the allowlisted user id(s) in
connectors.json `telegram.allow` are served (the first /start from anyone is answered with
their id so the owner can add it; nothing else is done for unknown users). Messages:
- plain text -> `chat.send` to the Operator in a thread `telegram-<date>`; reply with the answer
  (split at 4000 chars).
- a photo -> downloaded, `chat.send` with the image to the Critic, caption as text.
- `/today` -> Top 3; `/inbox` -> count and first 5; `/<bot command>` -> runs that bot now.
Approvals: whenever an inbox item of kind approval is created (connector.call, print, photos),
the gateway sends it with inline buttons Approve / Reject; the callback runs the same code path
as the dashboard (`inbox.set`), then edits the message to the result. Bot results with notify
also go to Telegram as a short message.

## 6. Activity: `aihq/activity.py`

`record(domain, who, connector, tool, args_summary, result_summary, status)` appends a JSON
line to `hq/os/activity/YYYY-MM-DD.jsonl` (who = app:<id> | operator | the owner id; status
done | queued | failed) and a `hq/log` line. `recent(domain, days=7, limit=200)`.
Snapshot key `activity` (last 50). Overview shows "What the bots did" (last 10, plain
sentences: "Show Scout put 'TPS Members Show: entries close' on your calendar, Dec 15").

## 7. UI (web/)

- **Connectors** panel in YOUR WORKSPACE nav: one card per connector with status, the secrets
  it needs (masked input -> POST /api/secrets; shows "saved" never the value), Test (calls a
  read tool), Enable/Disable; "Add MCP server" form (id, name, transport stdio|http, command +
  args or url, env/headers as KEY=secret rows) writing hq/os/connectors.json through
  `connectors.add_mcp(...)`; listed tools per connector.
- **Models** under Connectors: provider keys (masked) and the model list; the chat composer and
  each bot's Settings get a model select.
- **Bot Settings** (agent-app.js): triggers editor, connectors checkboxes, per-action policy
  (auto / ask) for the actions those connectors expose, model select.
- **Activity** panel and the Overview strip.

## 8. Rules

1. Secrets never in the repo or in any API response.
2. Outside actions go through ConnectorTools so policy and Activity always apply.
3. Print, Photos and governance are always "ask".
4. A connector failing never takes the OS down; its card says why.
