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

Reusable app definitions live in versioned `apps/<slug>/` with a manifest, specification and handoff. Private project notes and legacy feature/review records live under `AIHQ_HOME/projects/<slug>`, outside Git. Operational events and memory live in the private SQLite database. Promote only reviewed, generic lessons into `vault/` through a Git change. Product source can live in a bounded folder or separate repository with its own tests and CI.

## Agent delegation

Hermes can coordinate; Codex/Devin/Claude can implement. Before asking another agent to work, write a bounded task: context, files, acceptance tests, excluded files, tool permissions, expected evidence and stop conditions. Independent review is a *separate* assignment; do not treat the builder's self-evaluation as independent.

## On return from a break

Read `PROJECT.md`, `docs/ROADMAP.md`, `docs/DECISIONS.md`, `docs/HANDOFF.md`, current branch and CI state. Summarize observed status and only then continue. Don't recover history by inventing events. Record what is unknown.

## State and UX limitations

The dashboard runs declarative apps, local tasks, memory, approvals and interval scheduling controls. Interview previews require approval before writing versioned definitions. Built-in work runs through the local worker; external agents additionally require exact-request approval and an explicitly enabled CLI worker. Model/provider setup and external isolation remain separate. The dashboard does not merge PRs, deploy production software or certify CI. See `docs/CAPABILITIES.md` for precise implementation and verification limits.
