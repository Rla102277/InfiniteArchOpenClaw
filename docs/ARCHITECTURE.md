# InfiniteArch Agentic OS architecture

The portable brain is a GitHub-reviewed checkout of reusable definitions. The runtime is a private local view of that brain plus local records. GitHub is the canonical source for definitions; it is not the database or a production deployment service.

## Components and authority

| Component | Responsibility | Boundary |
| --- | --- | --- |
| `system/os.json` | Version and default adapter/theme | Configuration only |
| `system/contracts/` | Manifest, approval, storage, scheduler, builder, and GitOps rules | Requirements do not enforce themselves |
| `vault/` | Obsidian-compatible reusable semantic memory | No private user memory |
| `agents/` | Hermes coordinator; Codex, Claude, Devin implementers | Capability declarations, not credentials or OS grants |
| `apps/` | Three seeded record boards and approved generated declarations | Shared renderer; no generated executable code |
| `themes/` | Forest, Paper, Midnight color values | No scripts or remote resources |
| `workflows/` | Fixed `digest.create` declaration | No agent invocation |
| Local runtime | SQLite tasks, approvals, schedules, memory, records, activity | External private root |
| Loopback dashboard | Local state, explicit actions, previews, owner decisions | No agent, shell, or Git execution from HTTP |
| Explicit CLI worker | Deterministic local jobs; separately gated adapters | External adapters require approval and `--allow-external` |

The data root is `AIHQ_HOME` or `~/PersonalAIHQ`; SQLite lives at `runtime/os.sqlite3` beneath it. Private documents, local project files, secrets, operational logs, and exports stay under this external root or in the platform credential store. Existing local project records use the root's `projects/` directory. See [STORAGE.md](../system/contracts/STORAGE.md); the canonical `vault/` must never become a copy of private notes.

## Data flow

Read and validate canonical JSON → explicitly reconcile local state → show available declarations → record owner choices locally. Exactly three generic apps ship: Project Board, Reading Room, Daily Planner. Available does not mean installed or enabled. Install and enable through the local lifecycle before adding private records; pausing or uninstalling must retain those records.

For the builder: interview → preview → explicit approval → write `apps/<id>/app.json`, `SPEC.md`, and `HANDOFF.md` → reconcile → install/enable the shared record board. All generated files are declarations or Markdown. Arbitrary generated application code is not supported. See [BUILDER.md](../system/contracts/BUILDER.md).

The scheduler runs the deterministic local digest operation while a worker is running. It cannot launch an agent. HTTP can queue a request and record its approval; external execution requires a separate CLI worker with `--allow-external`. Hermes, Codex, and Claude need configured local/provider access. Devin is handoff-only. See [TASKS_AND_APPROVALS.md](../system/contracts/TASKS_AND_APPROVALS.md) and [ADAPTERS.md](../system/contracts/ADAPTERS.md).

## Change and rollback

Approve a bounded change, preview its exact diff, validate manifests, run checks, record actual evidence, obtain independent review when available, and accept the demo. Merge and release remain separate human decisions. An explicitly approved update uses fast-forward-only GitOps with an earlier exact preview; reconciliation does not approve a pull or execute a changed skill.

Record the previous brain revision and a consistent external backup before migration. Recover code in a separate clean checkout or use an independently reviewed revert for shared history. Recover private state into a separate external root and verify it before switching. Never erase local records to roll back an app definition. See [PORTABILITY.md](PORTABILITY.md) for the recovery sequence.

## Handoff and limitations

Use [HANDOFF.md](HANDOFF.md) to transfer objective, file ownership, revisions, observed checks, unknowns, and rollback. Adapter interchangeability does not transfer approvals, credentials, skill trust, or provider sessions. A handoff selected with `--task` includes its private prompt and must be reviewed before sharing.

[CAPABILITIES.md](CAPABILITIES.md) distinguishes bounded local features from unverified integrations. Cloud services have not been live-tested here; installers have not been run; a Mac mini has not been provisioned; no production deployment has occurred. These contracts do not supply a sandbox, remote multi-user authentication, or arbitrary code generation.
