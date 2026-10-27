# Capabilities and evidence boundaries

The supported product is a local agentic workspace with portable declarations and a bounded declarative app builder. “Working” below describes the implementation boundary; it is not a claim of completed hosted CI, cloud integration testing, owner acceptance, or production readiness. Check [HANDOFF.md](HANDOFF.md) for observed verification.

| Capability | Scope | Evidence boundary |
| --- | --- | --- |
| Canonical brain | JSON contracts, agent declarations, themes, workflows, generic app definitions, Obsidian-compatible semantic memory | Source files are inspectable; validation must be run |
| Seed app catalog | Exactly Project Board, Reading Room, Daily Planner, initially available | Availability does not imply installed/enabled or seeded personal records |
| Local record boards | Shared renderer with title, status, note; lifecycle and private SQLite records | Bounded local feature; no arbitrary app code |
| Declarative builder | Interview, preview, explicit approval, app manifest plus `SPEC.md`/`HANDOFF.md` | Working bounded builder; verify through the runtime tests |
| Local tasks and memory | Persistent runtime database at external root | Local feature; not remote shared storage |
| Digest scheduling | Deterministic `digest.create` with `{}` while a worker runs | No model calls, calendar integration, or background service provisioning |
| Approval queue | Per-request external-agent approval and context checks | Not an OS sandbox, authenticated multi-user authorization, merge, or deployment approval |
| Hermes | Coordinator declaration, planning/session interfaces, configured adapter | Cloud service not live-tested |
| Codex and Claude | Interchangeable bounded implementation destinations; configured CLI adapters | Provider authentication and live execution not tested here |
| Devin | Manual portable handoff packet only | No cloud submission, execution, or polling |
| Optional local model adapter | Separate configured local service if provided by the runtime | No model downloaded or live-tested here |
| GitOps | Validation/reconciliation and explicitly approved fast-forward-only CLI pull | No HTTP Git execution, automatic push, merge, or release |
| Backup and handoff | Explicit local backup; default handoff excludes private state | Recovery and target-machine drills still require evidence; `--task` includes a private prompt |
| CI | Ubuntu/macOS × Python 3.12/3.14; shell checks, static verification, unittest, unconditional CLI validation | Hosted matrix has not been observed merely because YAML exists |

## Deliberate limitations

Cloud services have not been live-tested. Installers have not been run. A Mac mini has not been provisioned. No production deployment has occurred. No arbitrary generated application code is supported yet; the working builder produces declarations and Markdown for fixed templates.

Agent skill and permission arrays are capability descriptions, not enforced filesystem sandboxes. The host session, runtime checks, and explicit approval determine effective authority. Do not infer full enforcement of every normative contract from catalog validation alone. HTTP never launches agents; a separate CLI worker with `--allow-external` is required for approved external requests.

The exact task/approval shapes and current gaps are in [TASKS_AND_APPROVALS.md](../system/contracts/TASKS_AND_APPROVALS.md). Catalog validation does not yet reject every unknown/duplicate key or enforce all exact schema values. Builder confirmation is not a cryptographically bound, expiring preview token, and the three generated files are not written as an atomic bundle. Schedule state has no per-schedule workflow-revision binding or timezone setting. Backup snapshots are not encrypted and contain the database only. See the contract-specific implementation notes before relying on stronger guarantees.

Schema migration, encrypted storage, remote multi-user access, production service management, and disaster recovery require separate review and evidence. These files do not authorize installs, paid service calls, publication, or deployment.
