# Task and approval boundary

The local queue and the engineering delegation process have different purposes. A runtime task requests one allowlisted operation; it is not a general command runner or a grant to implement source changes. All runtime tasks, decisions, prompts, and evidence are private local state.

## Exact local task interface

Submit a JSON action object with `action: "task.create"`, a nonempty `title`, an allowlisted `operation`, and a `payload` object. The accepted operations are:

| Operation | Normalized payload | Initial status |
| --- | --- | --- |
| `digest.create` | Exactly `{}` | `ready` |
| `memory.put` | Nonempty string `key` and `content` | `ready` |
| `agent.prompt` | String `adapter`, nonempty `prompt`, string `model` | `awaiting_approval` |

The runtime supplies a default model value when omitted. Only explicitly configured adapters are usable; Devin rejects queued execution and is handoff-only. Do not put shell commands or file execution fields in payloads. The operation-specific validator normalizes input; callers cannot invent additional operations.

The stored task has `id`, `title`, `operation`, `payload`, `status`, `result`, `error`, `created`, `updated`, and nullable unique `dedupe_key`. The database stores the payload as JSON text and timestamps numerically; the state view exposes the payload as an object and timestamps as UTC strings. Status values are `awaiting_approval`, `ready`, `running`, `succeeded`, `failed`, and `canceled`. Only queued `ready` or `awaiting_approval` tasks can be canceled. Recovering an interrupted `running` request marks it failed with an unknown outcome; it must not silently rerun an external side effect.

## Exact local agent approval

Every `agent.prompt` task creates one approval record with `id`, unique `task_id`, `title`, `status`, `adapter`, and `prompt_hash`. Status is `pending`, `approved`, or `rejected`. The trusted local owner interface submits `action: "approval.decide"`, the approval `id`, and `decision: "approved"` or `"rejected"`. A model response cannot approve itself.

`prompt_hash` binds the normalized payload, canonical brain fingerprint, Git HEAD, executable path, and executable content hash where applicable. Canonical binding uses SHA-256 over `json.dumps(value, sort_keys=True, separators=(",", ":"))` encoded as UTF-8, with Python's default ASCII escaping. Recheck that binding at approval and immediately before execution. Changed prompt, adapter, executable, revision, or brain content requires a new request. Reconciliation does not refresh an existing approval; its binding still must match. A pending approval cannot execute a task; rejecting it cancels the queued request.

Execution requires all three gates: approved request, unchanged bound context, and a separately started CLI worker with `--allow-external`. Ordinary ticks and HTTP-triggered ticks exclude `agent.prompt`. Claim a ready task transactionally before execution; terminal tasks do not return automatically to the queue. Approval status is a decision record, not an independently reusable bearer token.

This is single-user local authorization, not authenticated multi-user identity. The current record does not claim expiry timestamps, signed remote approvals, or an OS permission sandbox. `agents/*.json` skill and permission arrays describe capability ceilings; they are not host-enforced filesystem access controls. Provider tools and the local user account may have broader access. Do not claim source-write isolation solely from those declarations.

## Engineering delegation scope

Before a coordinator delegates implementation, include objective, reviewed base revision, adapter, allowed and excluded files, requested tool permissions, observable acceptance checks, expected evidence, rollback, and stop conditions in the human-approved packet. Exclusions win. Unknown permissions are denied by policy; file paths must remain in the approved checkout. No packet grants access to secrets, production credentials, messaging, spending, merge, or deployment unless separately authorized. This written scope is an engineering contract, not additional fields already enforced by the local queue schema.

Independent review is a separate assignment, and builders cannot certify their own independence. Stop on scope expansion, missing approval, or three unsuccessful bounded repair attempts. Changing adapters or scope needs renewed review; handoff does not transfer approval or credentials.

## Distinct decisions

| Decision | Permitted effect | Does not authorize |
| --- | --- | --- |
| Specification approval | One bounded engineering task | Expanded scope or provider credentials |
| Builder approval | Previewed app JSON and specification/handoff Markdown | Arbitrary generated code, commit, push, or deployment |
| Agent approval | One bound queued request, when the CLI worker is explicitly enabled | Other prompts, agents, or changed context |
| Demo acceptance | Record the owner's feedback | Claiming CI passed, Git merge, or release |
| GitOps pull approval | Earlier exact preview, fast-forward-only CLI update | Rebase, reset, push, or skill trust |
| Schedule creation | Recurring local `digest.create` with the chosen interval | Agent invocation, cloud calls, or another operation |
| Release approval | Separately reviewed release procedure | Any other deployment or financial action |

Builder and GitOps confirmations are separate flows, not records in the agent approval table. The builder previews the full declaration bundle and requires an explicit confirmation. GitOps confirmation must match an earlier exact pull preview. See [BUILDER.md](BUILDER.md) and [GITOPS.md](GITOPS.md).

HTTP enforces loopback host/origin checks, bounded JSON input, and anti-CSRF protection for writes. It can queue requests and record local decisions but must never launch an agent, shell, installer, Git pull, or arbitrary executable. Host isolation, credential controls, and deliberately reviewed provider access remain necessary.
