# THE INFINITE ARCH Agentic OS: status against the spec

Status legend: Verified = observed this session (test file and case, or command output).
Built, not verified = implemented, no observed evidence here. Partial = missing pieces.
Not built = absent. When in doubt the lower status is used.

## Requirements OS-001 to OS-020

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 001 | GitHub-first brain | Verified | tests/test_gitbrain.py, tests/test_gitops.py pass in both repos |
| 002 | One-command bootstrap | Verified | tests/test_bootstrap.py, test_fork_push.py pass |
| 003 | Local-first operation | Verified | loopback-only server: tests/test_server.py, test_os_server.py |
| 004 | Interchangeable AI engines | Verified | tests/test_providers.py, test_agentrun.py, test_llm.py (Anthropic, OpenAI-compat, Ollama, adapter CLIs) |
| 005 | Conversational interface | Verified | tests/test_chats.py; /api/chat exercised on live instance :18731 |
| 006 | Application factory | Partial | Agent apps (standing prompt, schedule, records, chat) are created, updated and retired from conversation (tests/test_agentapps.py). Not built: generating complete applications with their own code, data model, UI and deployment lifecycle; the declarative builder was removed Oct 29 2026. |
| 007 | Agent Studio | Partial | Agent proposals and acceptance, skills, and per-app model, connectors, policy and triggers exist (tests/test_agentops.py, tests/test_bots.py). Not built: spending limits, version history and rollback, memory policy, per-agent knowledge boundaries. |
| 008 | Skills framework | Verified | tests/test_agentops.py::test_add_skill_writes_skill_md_and_reconciles; test_process.py |
| 009 | Workflow automation | Verified | tests/test_runtime.py (schedules, worker ticks), catalog workflows |
| 010 | Personal Life HQ | Partial | Today view, recos, memory and the calendar connector exist. Not built: OS-owned tasks, reminders, projects and goals, routines. |
| 011 | Knowledge vault | Verified | vault/ in catalog; memory tools in tests/test_basetools.py |
| 012 | Git-native handoff | Verified | tests/test_runtime.py::test_handoff_excludes_private_memory_records_approvals_and_credentials |
| 013 | Mission Control | Partial | Runs, schedules, approvals, activity and failures are visible (dashboard, /api/v1, tests/test_apiv1.py). Not built or not verified: resource utilization, queue depth and duration, retry, modify or cancel of a failed run from the UI. |
| 014 | Themeable interface | Verified | 4 themes; tests/test_apiv1.py::test_all_themes_meet_wcag_aa |
| 015 | Security and governance | Verified | CSRF/host/origin tests; approval binding tests in test_runtime.py, test_adapters.py |
| 016 | GitOps / Brain Sync | Verified | tests/test_gitsync.py (failure reporting, EDEADLK retry) |
| 017 | Portable deployment | Partial | The brain is in Git and local workspace seeding is tested (tests/test_seed_local.py). Not built: restoring private state from a backup onto another Mac. |
| 018 | Self-evolution | Partial | agent proposals and app self-edit exist; no self-modifying approved skills |
| 019 | Generic distribution | Partial | The published core passes tests/test_generic_core.py. Still practice-coupled in core: the --darkroom install-service flag and the Overview sections keyed to curator/darkroom app ids in web/app.js and web/index.html. |
| 020 | Complete deliverable | Partial | docs, CI and bootstrap ship; onboarding flow is fork-first and untested end to end on a fresh Mac |

## Acceptance tests

| # | Acceptance test | Status | Evidence |
|---|---|---|---|
| 1 | Fresh Mac setup | Partial | test_bootstrap.py/test_fork_push.py cover guards; no real fresh-Mac run observed |
| 2 | First push | Built, not verified | fork_push tests cover the flow; no real push observed in OpenClaw |
| 3 | Local inference | Built, not verified | Ollama provider tests pass; no live model call observed |
| 4 | Knowledge persistence | Verified | test_gitbrain.py memory rebuilds from Git |
| 5 | Agent creation | Partial | Proposal to acceptance is tested (tests/test_agentops.py); creating, testing and activating a specialist agent end to end has not been observed. |
| 6 | Scheduled execution | Verified | test_runtime.py schedule/worker tests |
| 7 | Application creation | Partial | Same reason as 006. |
| 8 | Application modification | Partial | Agent app update and retire are tested; a conversational feature request producing a tested upgrade of a generated application is not built. |
| 9 | Monitoring | Partial | Real runs and failures are visible; resource usage is not built. |
| 10 | Governance | Verified | approval-gated actions in test_runtime.py, test_adapters.py |
| 11 | Theme customization | Verified | theme catalog + WCAG test |
| 12 | Cross-agent handoff | Verified | handoff export test in test_runtime.py |
| 13 | GitOps | Verified | test_gitops.py preview/apply/invalidation |
| 14 | Backup and restore | Not built | ./hq backup writes a local SQLite backup; there is no restore command and no procedure to rebuild on another Mac. |
| 15 | Regression testing | Verified | ./hq test: 265 tests, OK, exit 0 |

## Spec layout to real paths

| Proposed | Real |
|---|---|
| brain/ | system/, agents/, workflows/, themes/, vault/, .agents/, AGENTS.md (the Git catalog) |
| control-plane/ | aihq/ (runtime, adapters, providers, server), hq CLI |
| interface/ | web/ (dashboard), web/command.* (WHOPR) |
| adapters/ | aihq/builtins/ connectors + aihq/adapters.py runtimes |
| runtime state | AIHQ_HOME (~/PersonalAIHQ), never committed |
| apps | apps/<id>/ module apps and hq/os/apps/<id>/ agent apps |

