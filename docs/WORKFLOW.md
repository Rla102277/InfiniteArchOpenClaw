# The owner-friendly engineering workflow

The user never needs to know an agent's skill name. Recognize conversational intent and use the correct workflow.

| Stage | Output | Hard gate |
| --- | --- | --- |
| Discover | Original idea, clarified purpose and people | Don't guess an unstated objective |
| Wayfind | Scope, research, prototype options, first slice | Owner understands and chooses direction |
| Specify | Acceptance criteria and explicit exclusions | Owner approves scope before implementation |
| Architect | Options, interfaces, data, security, portability, rollback | Record decision and responsible approver |
| Build | One scoped feature branch with tests | No broad unattended change |
| Verify | Real command output, test logs and known limitations | Never claim green without actual result |
| Review | Independent findings and bounded repair | Failure blocks readiness; no self-signoff |
| Demo | Working behavior and human acceptance | HQ decision records demo feedback only |
| Merge/release | Green CI and explicit authorized approval | Never automatic in starter |
| Reflect | Decisions, lessons, handoff, next slice | No loss of context between sessions |

## Durable artifacts

Every project lives under `projects/<slug>` with `PROJECT.md`, `project.json`, a roadmap, decisions, handoff, feature requests, review requests and an append-only activity log. Source code for a product should live in a bounded project folder or separate repository as complexity grows; keep its own tests and CI.

## Agent delegation

Hermes can coordinate; Codex/Devin/Claude can implement. Before asking another agent to work, write a bounded task: context, files, acceptance tests, excluded files, tool permissions, expected evidence and stop conditions. Independent review is a *separate* assignment; do not treat the builder's self-evaluation as independent.

## On return from a break

Read `PROJECT.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/HANDOFF.md`, current branch and CI state. Summarize observed status and only then continue. Don't recover history by inventing events. Record what is unknown.

## State and UX limitations

The first dashboard lets people record and accept a demonstration. It does not create branches, check GitHub's CI state, evaluate code, merge PRs, or control external agents. Those are separate verified steps that must be implemented before this can operate autonomously.
