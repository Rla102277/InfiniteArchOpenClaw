# Interchangeable agents

Hermes coordinates discovery, specification, architecture, bounded delegation, and handoff. Codex and Claude can implement the same task envelope. Devin is an interchangeable implementer destination for a **handoff-only** packet: the starter does not submit jobs to Devin, poll its service, or execute its output. Registering an adapter never installs it or authenticates a cloud service.

The transport can change while the task objective, allowed files, exclusions, acceptance checks, stop conditions, evidence format, and rollback remain intact. Changing the adapter requires approval of the revised task; it does not inherit another provider's approval or credentials. Adapters must report unavailable capabilities explicitly.

## Permission vocabulary

| Permission | Maximum meaning |
| --- | --- |
| `tasks.read` | Read the approved task packet, excluding secrets |
| `tasks.propose` | Draft a bounded task for owner review |
| `approvals.request` | Request a human decision; never grant it |
| `workspace.write` | Change only approved files in an isolated implementation session |
| `verification.run` | Run the task's reviewed checks in that session |
| `handoff.prepare` | Prepare a packet for manual transfer; no network submission |
| `local.records` | Access local board records through validated app handlers |

The manifest lists the ceiling; [task and operation approval](TASKS_AND_APPROVALS.md) and host policy determine actual permission. `local.records` cannot read the secrets store or private documents outside the selected app scope. No adapter has implicit merge, release, messaging, spending, installer, or production privileges.

## Required return packet

Return the task ID, reviewed base revision, resulting revision or patch, changed files, acceptance results with command/exit code and evidence location, unresolved risks, rollback steps, and next action. Label observations, inferences, unverified claims, and proposals. Keep private evidence in external local storage and transfer only a deliberately reviewed, redacted packet to a provider. Preserve authorship so another independent reviewer can assess the work; a builder assigned the `review` skill cannot certify independence on its own task.

Adapters are integration declarations, not evidence that Hermes, Codex, Claude, or Devin cloud services have been live-tested. HTTP and deterministic scheduler handlers cannot launch any adapter.
