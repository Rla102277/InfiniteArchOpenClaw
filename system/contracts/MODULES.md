# App modules, version 1

The OS (`os/`) is the host: apps, agents, tasks and approvals, skills, schedules, routines,
memory, usage, activity, system. A **module app** is a full application that mounts into it:
its own Python, its own views, its own agents and routines, under the OS's rules (loopback only,
CSRF, approvals, Git brain).

Record-board apps (`kind: record-board`) are unchanged. This contract adds `kind: module`.

## Manifest: `apps/<id>/app.json`

```json
{
  "schema_version": 1,
  "id": "notes",
  "name": "Notes",
  "description": "A notes desk: inbox, ask, boards, memory, agents.",
  "kind": "module",
  "entry": "notes.app",
  "icon": "◈",
  "views": [
    {"id": "inbox", "label": "Inbox", "count": "inbox_open"},
    {"id": "ask", "label": "Ask"},
    {"id": "boards", "label": "Boards"},
    {"id": "memory", "label": "Memory"},
    {"id": "agents", "label": "Agents"}
  ],
  "web": {"script": "web/module.js", "style": "web/module.css"},
  "permissions": ["hq.write", "model.call"],   // <app>.read/.write permissions gate calls into other module apps after an approval
  "version": "1.0.0"
}
```

`entry` is a dotted module name resolved inside `apps/<id>/` only (the loader puts `apps/<id>`
on `sys.path` and refuses anything else). `views[].count` names a key in the module snapshot's
`counts` dict for the nav badge. `web.script` and `web.style` are served at
`/apps/<id>/<path>`; only files under `apps/<id>/web/` are served.

## Python: `apps/<id>/<entry>.py` exposes `create(host) -> App`

```python
class Host:                 # given by aihq.modules
    os_root: Path           # os/
    domain: Path            # repo root
    app_dir: Path           # apps/<id>
    app_id: str
    def log(self, event: str, message: str) -> None      # OS activity + hq/log line
    def enqueue(self, title: str, routine: str) -> dict  # queue this module's routine as an OS task
    private_dir: Path                                    # throwaway state, outside the checkout
    def app(self, app_id: str) -> App | None             # another loaded module app, for app-to-app calls

class App:
    id: str
    def snapshot(self) -> dict        # anything JSON; MUST include "counts": {<name>: int} (may be empty)
    def action(self, payload: dict) -> dict      # payload["action"] is the module's own verb
    def ask(self, text: str, agent: str | None) -> dict   # optional; synchronous answer
    def routines(self) -> list[dict]  # [{"id","name","every": "6h"} or {"id","name","at": "06:30"}]
    def run_routine(self, routine_id: str) -> dict
    def agents(self) -> list[dict]    # [{"id","name","role","description","last_run","tokens_today"}] for the OS Agents panel
    def usage(self) -> dict           # {"today": int, "7d": int, "30d": int, "runs_30d": int, "by_agent": {...}}
    def cli(self, argv: list[str]) -> int   # ./hq app <id> <argv...>
    def doctor(self) -> list[dict]    # [{"status": "ok|info|FAIL", "name", "detail"}]
```

Everything optional except `id`, `snapshot`, `action`. Missing methods are treated as empty.
Exceptions in any method are caught by the host and reported as `{"error": "..."}`; one broken
app never takes the dashboard down.

## Host side (aihq)

- `aihq/modules.py`: `load(brain) -> dict[id, App]`, cached per catalog fingerprint. Import
  failures are recorded and shown in Applications as `status: broken` with the message.
- Runtime snapshot gains `modules: {<id>: {"manifest": {...}, "snapshot": {...} | {"error"}}}`,
  and the OS **Agents**, **Routines** and **Usage** panels include module data (tagged with
  the app id) next to the OS's own.
- Actions: `module.action {app, payload}` and `module.ask {app, text, agent}` delegate.
  `module.routine {app, routine}` runs one now.
- Scheduler: every module routine is registered as a workflow `module:<app>:<routine>` so it
  can be scheduled like any other, and the app's declared cadence creates the schedule on first
  reconcile if none exists (enabled). Module routines run in the worker like built-in tasks:
  no approval, because they only write under `hq/`.
- Server: `GET /apps/<id>/<file>` (static, under `apps/<id>/web/` only, same CSP),
  `GET /api/apps/<id>/snapshot`, `POST /api/apps/<id>/action`, `POST /api/apps/<id>/ask`
  (CSRF like every POST).
- CLI: `./hq app <id> [args...]` calls `App.cli`; `./hq doctor` appends each app's `doctor()`.
- Module apps appear in Applications with status `enabled` by default (not installable or
  uninstallable: they are code, reviewed in Git).

## Web: `apps/<id>/web/module.js`

Loaded once by the shell. It registers itself:

```js
window.OSModules = window.OSModules || {};
window.OSModules["<id>"] = {
  mount(container, host) {},           // once; build the DOM inside container
  render(snapshot, view) {},           // every refresh (5s) and on view change; snapshot = module snapshot
  unmount() {}                         // optional
};
```

`host` gives the module what the shell already has, so modules do not re-implement it:

```js
host = {
  appId,                               // the manifest id
  get(path),                           // GET /api/apps/<id>/<path>, returns JSON
  post(path, body),                    // POST /api/apps/<id>/<path> with CSRF, returns result
  action(payload),                     // POST action, returns result
  ask(text, agent),                    // POST ask
  el(tag, text, className), button(label, onClick, className), badge(status),   // shell helpers
  announce(text, isError),             // the shell's notice bar
  refresh(),                           // ask the shell to refresh now
  navigate(view)                       // switch to one of this app's views
}
```

The shell renders the app's nav entries under an **APPS** heading (icon, name, then its views
indented, with badges from `counts`), keeps the hash as `#app/<id>/<view>`, and gives the module
one `<section id="module-<id>">` to own. The module's CSS is scoped: every selector starts with
`#module-<id>`. Theme colors come from the shell's CSS variables (`--background`, `--panel`,
`--text`, `--muted`, `--accent`); modules use them and define none.

## Rules

1. A module writes only under `hq/` (the Git brain). The host does not enforce this at the OS
   level; the module's own tests must.
2. No module ships a server, a CLI binary, a package install or a network listener.
3. No em dashes anywhere.
