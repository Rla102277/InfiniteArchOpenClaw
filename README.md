# InfiniteArch Agentic OS

A GitHub-native, local-first agent workspace. Your repository is the versioned brain. Your machine holds private records, credentials, memory and execution state. Any coding agent can read the same contracts and continue the work.

This is an application and coordination layer, not a replacement macOS kernel. Built-in operations need no AI subscription or third-party Python packages. Hermes is the preferred conversational coordinator; providers and coding tools remain replaceable.

## Start

Fork this repository, clone **your fork**, then:

```bash
./bootstrap.sh
./hq start
```

The dashboard opens at `http://127.0.0.1:8731`. Bootstrap never executes remote installers or weakens macOS security. The first onboarding push requires a typed `PUSH`, verifies fork ownership and supports retry without duplicate commits. Python 3.10+ and Git support local operation; GitHub CLI supports fork onboarding. See [Start here](START_HERE.md) and [fork walkthrough](docs/FORK_START.md).

Without onboarding or GitHub authentication:

```bash
./hq validate
./hq init
./hq start --no-browser
# In another terminal:
./hq worker
```

## Working features

- **App builder:** interview → Wayfinder plan → exact specification preview → explicit approval → runnable declarative record board. Install, enable and use the app in the dashboard.
- **Private operations:** SQLite tasks, app records, memory, approvals, activity history and dashboard preferences survive restarts outside the checkout.
- **Automation:** interval schedules execute versioned allowlisted workflows. The included daily digest updates persistent memory without a model. Missed intervals coalesce; a worker must be running.
- **Control center:** three versioned themes, customizable visible panels, app lifecycle, task results, memory, agent availability and truthful capability statuses.
- **Approvals:** external requests require approval bound to the payload, brain fingerprint, Git revision and executable. Browser ticks execute built-in operations only. External execution also requires `./hq worker --allow-external` locally.
- **Portable GitOps brain:** Markdown vault, first-party skills, contracts, manifests and handoffs. Validate/reconcile locally; preview and confirm clean fast-forward GitHub pulls. No automatic push, merge or deployment.

## GitHub versus machine

| Versioned brain | Private local state |
| --- | --- |
| `AGENTS.md`, `system/`, `vault/`, `.agents/skills/` | Provider credentials in native stores/Keychain |
| `apps/`, `agents/`, `workflows/`, `themes/` | `~/PersonalAIHQ/runtime/os.sqlite3` |
| Architecture, handoffs and tests | `~/PersonalAIHQ/private/`, `secrets/`, `logs/`, `exports/` |

Set `AIHQ_HOME` to an absolute private directory outside the checkout to change its location. Generated app specifications intentionally enter Git only after your approval: keep their descriptions generic. Legacy project/review records now also stay in the private workspace.

## Agents and boundaries

Hermes, Codex and Claude adapters invoke installed CLIs without permission-bypass flags. Authentication and model setup are separate. Codex requests use read-only sandbox; Claude requests use plan mode with built-in tools disabled. Hermes retains configured native tools and approvals. An OS approval authorizes starting the selected program; **it is not a process sandbox**. Ollama also supports direct inference at a fixed loopback endpoint with an already downloaded model.

**Devin is handoff-only.** Paid/cloud agent calls were not live-tested. Arbitrary application source generation, remote multiuser access, messaging integrations and production deployment are not claimed as implemented. Mac mini setup is documented, not provisioned. See [capabilities](docs/CAPABILITIES.md), [Hermes](docs/HERMES.md), [security](docs/SECURITY.md) and [architecture](docs/ARCHITECTURE.md).

```bash
./hq test
./hq validate
./hq handoff
./hq reconcile
./hq reconcile --pull             # fetch and preview; clean tracking branch required
# Review git diff HEAD..@{u}, then:
./hq reconcile --pull --confirm
```

[Runtime examples](docs/RUNTIME.md) cover tasks, schedules and backups. [Portability](docs/PORTABILITY.md) covers agent/machine handoffs. CI checks contracts and tests on macOS and Linux. The [handoff](docs/HANDOFF.md) records limitations and rollback.

The ZIP in `releases/` is an obsolete v0.3 archive retained for history. Clone current source for this OS.
