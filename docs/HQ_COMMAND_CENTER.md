# Infinite Arch headquarters

The headquarters is a selectable interface on the local HQ server. It is not the system of record and it is not an agent brain.

GitHub remains the authority for agent definitions, skills, workflows, and architecture. Operational records (tasks, schedules, runs, approvals, preferences, and conversation transcripts) stay in `.local/hq/` on this machine. That folder is gitignored.

## Open it

Start the local server with `./hq start`. Then open:

- Classic dashboard: http://127.0.0.1:8731/
- Headquarters: http://127.0.0.1:8731/command

Set `HQ_COMMAND_CENTER=0` before starting the server to hide the headquarters assets. The classic dashboard keeps working.

## What is live

- Workspace and project records already stored in `projects/`
- Versioned skills and repository documents
- Canonical agent definitions in `system/agents/`
- Adapter availability for Local HQ, Hermes, Codex, Devin, and Claude, using a PATH lookup. A missing executable is offline. The lookup is not a heartbeat.
- Tasks, schedules, runs, approvals, and events in the local database
- A manual or periodic local check that records adapter status, including an offline Hermes result with a timestamp
- Owner decisions for existing project reviews. Accepting a demo still does not merge or deploy.
- External-action schedules, which create an approval and do not send mail, call a provider, or run a shell command even after approval
- Theme, workspace, and layout preferences stored locally
- A read-only Git status summary. The shell does not commit or push.
- Proposed agent definitions written under `proposals/agents/` for you to review and commit yourself

## What is not wired

- The browser does not start Hermes, Codex, Devin, or Claude. `./hq chat` remains the way to open Hermes.
- Microphone audio is not uploaded. Voice out, if you turn it on, uses the browser speech engine, which may use that browser's own service.
- There is no Docker control, email, or third-party app launch.
- Demo mode is a labeled fixture set. It is not your activity.
- Mac installability as a home-screen app depends on the local browser. This repository does not add a launchd service.

## Themes

HUD, Dark, and Light are stored preferences. Switching theme does not clear the open domain. HUD uses the cyan and amber command-surface palette. Dark and Light are neutral high-contrast themes. Fonts are system fonts so the interface does not call a font host.

## Rollback

1. Stop the server.
2. Set `HQ_COMMAND_CENTER=0`, or check out the previous revision.
3. Delete `.local/hq/` to drop local tasks, schedules, runs, and preferences.
4. Delete uncommitted files in `proposals/agents/` if you do not want a proposed definition.

The classic dashboard routes stay in place either way.
