# Deterministic local scheduler

The workflow declaration selects the allowlisted operation `digest.create` with payload `{}`. The operation compiles a local summary from authorized local records; it does not call a model or execute a prompt. Identical authorized input snapshots and logical run time produce identical digest content. The current runner stores epoch times and exposes UTC timestamps; it schedules intervals rather than timezone-aware calendar events.

## Execution boundary

1. Load and validate the canonical workflow; reject unknown operations and nonempty payloads.
2. Store `id`, `name`, `workflow`, `interval_seconds`, `enabled`, and `next_run` only in the external private database. The current interval range is 60 seconds through one year. Cloning the brain never enables a timer.
3. Use a trusted local runner with fixed operation dispatch. Scheduled workflows cannot invoke dynamic imports, shell strings, subprocesses, `eval`, network requests, adapters, or arbitrary generated code. The separate external-task execution path requires individual approval and an explicitly enabled CLI worker.
4. For each due slot, transactionally enqueue a task with a unique dedupe key derived from the schedule ID and stored due time. Update the next due time in that transaction. Repeated ticks cannot enqueue a second task for that claimed slot. The task's digest result and terminal state are committed together.
5. Record local task outcome and activity. Failures must not enter an unbounded retry loop. Missed slots coalesce to one catch-up run, with the next due time set from the current tick plus the interval; the runner does not replay an unlimited backlog.

Creating or toggling a schedule is an explicit local owner action. It is separate from the external-agent approval table and covers the described recurring local digest only. The current schedule record does not bind a workflow revision or timezone and does not have a cryptographically recorded approval envelope. Canonical brain drift blocks execution until reconciliation; reconciliation is not evidence of fresh owner approval of every existing schedule. Review affected schedules whenever definitions change. The digest writes a summary to private runtime memory and cannot approve another operation.

HTTP may record a schedule setting or request an allowlisted local digest through validated handlers; it never executes an agent or a Git pull. A workflow file is not proof of a resident worker, timer service, OS service installation, or tested restart behavior. Report separately whether manual ticks, persistent timers, idempotency, and recovery have actual evidence. Per-schedule revision approval, calendar/timezone scheduling, durable service management, and automated failure alerts are not implemented by this declaration. Mac mini unattended scheduling requires a separate provisioning and recovery exercise.
