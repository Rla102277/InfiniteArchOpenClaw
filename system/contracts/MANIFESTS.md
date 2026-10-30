# Canonical declarations, version 1

These files are portable, reviewable data. They never grant OS permissions or execute instructions. UTF-8 JSON objects must have exactly the documented keys, without duplicate keys, comments, unknown fields, or executable extensions. Reject unsupported versions and invalid references before writes. Identifiers match `[a-z][a-z0-9-]{0,63}`; filenames and directory IDs must agree. Names and descriptions are nonempty strings. Resolve paths inside their declared roots and reject traversal and symlink escapes.

## System

`system/os.json` has exactly `schema_version`, `name`, `version`, `default_adapter`, `default_theme`. This release uses `1`, `InfiniteArch Agentic OS`, `1.0.0`, `hermes`, and `forest`, respectively. Defaults must resolve to installed agent and theme declarations.

## Apps

**Removed Oct 29 2026:** no record-board app ships any more and the builder is gone; the loader still validates this shape for compatibility. Apps are module apps, see [MODULES.md](MODULES.md).

Two kinds. `kind: module` is a full application that mounts into the OS; its manifest and host contract are in [MODULES.md](MODULES.md). `kind: record-board` (below) is a declarative board. Every record-board `apps/<id>/app.json` has this exact shape:

```json
{
  "schema_version": 1,
  "id": "project-board",
  "name": "Project Board",
  "description": "Organize generic project tasks and notes.",
  "template": "project-board",
  "kind": "record-board",
  "fields": ["title", "status", "note"],
  "permissions": ["local.records"],
  "version": "1.0.0"
}
```

`id`, `name`, and `description` describe the app. `template` is exactly one of `project-board`, `reading-room`, or `daily-planner`. The other values and array ordering are fixed as shown. There is no command, script, HTML, entrypoint, code, environment, or remote URL field. Each shipped app uses its matching template; the initial catalog contains exactly those three IDs. Builder-created declarations may use a new ID with one of these three templates after approval. They still use the shared renderer and fixed fields.

## Themes

`themes/<id>.json` has exactly `id`, `name`, `colors`. Colors has exactly `background`, `panel`, `text`, `muted`, `accent`, each a six-digit hexadecimal CSS color beginning with `#`. The shipped IDs are `forest`, `paper`, `midnight`. Theme values are colors, never raw CSS, remote resources, or scripts.

## Workflows

`workflows/daily-digest.json` has exactly `schema_version: 1`, `id: "daily-digest"`, a nonempty `name`, `operation: "digest.create"`, and `payload: {}`. Version 1 accepts no additional payload keys. A workflow describes an allowlisted local operation, not a timer, shell command, or agent prompt. Scheduling state is private; see [SCHEDULER.md](SCHEDULER.md).

## Agents

`agents/<id>.json` has exactly `schema_version: 1`, `id`, `role`, `adapter`, `skills`, `permissions`. `skills` and `permissions` are arrays of unique nonempty strings. IDs and adapters are `hermes`, `codex`, `claude`, `devin`. Hermes has role `coordinator`; the others have role `implementer`. Bundled skill names refer to `.agents/skills/<name>/SKILL.md`. Adapter declarations describe capability ceilings, not authentication or execution settings. See [ADAPTERS.md](ADAPTERS.md) for permission meaning and Devin's handoff-only boundary.

## Compatibility

Treat these schemas as canonical requirements. Runtime validation coverage must be reported from actual checks; the Markdown contract alone does not enforce a security boundary. A schema change requires migration notes, validation, human review, and a rollback plan. Never silently reinterpret an unknown field or fall back to running code.

Implementation note: the current loader checks important shape, ID, template, permission, color, and workflow constraints, but does not enforce every exact key set, reject duplicate JSON keys, validate every agent skill/permission entry, or verify every fixed version/default-adapter value. The seed files conform to the stricter schema above; passing the current `validate` command is not evidence that all malformed declarations are rejected. The runtime also recognizes an optional local `ollama` adapter, which is not a shipped agent declaration.
