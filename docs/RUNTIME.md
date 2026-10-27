# Local runtime

Run examples from the clone with `python3` available. Set `AIHQ_HOME` to a private absolute directory outside the checkout before initialization if desired.

## Tasks and memory

```bash
./hq init
./hq action '{"action":"task.create","title":"Remember a preference","operation":"memory.put","payload":{"key":"planning","content":"Prefer three priorities each day."}}'
./hq worker --once
./hq state
```

The task and memory persist across restarts. The dashboard exposes the same actions. `state` includes private data; do not paste it into public issues.

## Schedules

```bash
./hq action '{"action":"schedule.create","name":"Daily digest","workflow":"daily-digest","interval_seconds":86400}'
./hq worker
```

The worker polls every five seconds and prints processed task IDs. Transactions claim due schedules once per due instant. Missed intervals coalesce into one job, then the next run is scheduled from the current tick. There is no calendar/cron parser. Ctrl-C stops the worker; pausing a schedule does not cancel already queued jobs.

## External requests

```bash
./hq action '{"action":"task.create","title":"Review architecture","operation":"agent.prompt","payload":{"adapter":"hermes","prompt":"Read AGENTS.md and docs/ARCHITECTURE.md. Suggest a safe improvement. Do not edit files."}}'
```

Read the full prompt in the dashboard, then approve or reject it. Approval binds the payload, brain definitions, Git revision and installed executable. Changes invalidate approval. Opt in separately:

```bash
./hq worker --once --allow-external
```

HTTP ticks never execute external agents. CLIs can have plugins, hooks, credentials and filesystem permissions; this OS is not their sandbox. Hermes keeps its configured tools, Codex selects read-only sandbox, Claude selects plan mode without built-in tools. Direct Ollama requests are text-only and use the fixed loopback endpoint. There is no silent provider fallback.

For direct local inference use `adapter: ollama` with the exact downloaded `model` name. No model is downloaded by the OS. A missing model/server fails visibly. Failed/stale requests require a new task and fresh approval.

Agent text output is truncated to 64 KB and kept locally. Runs time out after 120 seconds; subprocess groups are stopped on timeout. Killing a worker can leave a task `running` with unknown outcome. Inspect/stop the old process before using the CLI-only `task.recover` action to mark it failed. Never assume external work is safe to repeat.

## Apps and themes

Builder preview is read-only. Approval writes `apps/<id>/app.json`, `SPEC.md`, `HANDOFF.md` to Git's working tree. It creates record boards with title/status/notes, not unrestricted code. A separate review, commit and push makes definitions canonical on GitHub.

Lifecycle: available → install → installed → enable → enabled → pause → paused. Paused apps can be reenabled. Uninstall retains records; reinstall restores use. Changed manifests require reinstall. Missing definitions become unavailable; private data remains.

Themes declare validated hex colors in `themes/*.json`. Select themes and visible panels in the dashboard. Changes persist locally; new versioned themes require reconciliation.

## Backup and handoff

```bash
./hq backup                  # preview only
./hq backup --confirm        # consistent SQLite backup in private exports/
./hq handoff                 # excludes private task content
./hq handoff --task TASK_ID   # explicitly includes the selected prompt
```

Backups contain unencrypted private data; protect them with encrypted storage. To restore, stop dashboard and worker, preserve the current database separately, then copy the selected backup to private `runtime/os.sqlite3` with owner-only permissions. Use the matching runtime version. Never put backups or credentials in Git. Approval hashes prevent reuse on changed brain/executable identities.
