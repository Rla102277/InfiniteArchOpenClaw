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
| 006 | Application factory | Verified | tests/test_agentapps.py (create via tools, chat, CLI) |
| 007 | Agent Studio | Verified | tests/test_agentops.py (proposals, accept, skills) |
| 008 | Skills framework | Verified | tests/test_agentops.py::test_add_skill_writes_skill_md_and_reconciles; test_process.py |
| 009 | Workflow automation | Verified | tests/test_runtime.py (schedules, worker ticks), catalog workflows |
| 010 | Personal Life HQ | Verified | arms domains (today/recos/memory/usage) tested in tests/test_arms.py |
| 011 | Knowledge vault | Verified | vault/ in catalog; memory tools in tests/test_basetools.py |
| 012 | Git-native handoff | Verified | tests/test_runtime.py::test_handoff_excludes_private_memory_records_approvals_and_credentials |
| 013 | Mission Control | Verified | dashboard + /command (WHOPR); screenshots /tmp/ia-drift/whopr-*.png |
| 014 | Themeable interface | Verified | 4 themes; tests/test_apiv1.py::test_all_themes_meet_wcag_aa |
| 015 | Security and governance | Verified | CSRF/host/origin tests; approval binding tests in test_runtime.py, test_adapters.py |
| 016 | GitOps / Brain Sync | Verified | tests/test_gitsync.py (failure reporting, EDEADLK retry) |
| 017 | Portable deployment | Verified | tests/test_seed_local.py (symlink-safe local workspace seeding) |
| 018 | Self-evolution | Partial | agent proposals and app self-edit exist; no self-modifying approved skills |
| 019 | Generic distribution | Verified | tests/test_generic_core.py; publish_core.py; this repo is the published core |
| 020 | Complete deliverable | Partial | docs, CI and bootstrap ship; onboarding flow is fork-first and untested end to end on a fresh Mac |

## Acceptance tests

| # | Acceptance test | Status | Evidence |
|---|---|---|---|
| 1 | Fresh Mac setup | Partial | test_bootstrap.py/test_fork_push.py cover guards; no real fresh-Mac run observed |
| 2 | First push | Built, not verified | fork_push tests cover the flow; no real push observed in OpenClaw |
| 3 | Local inference | Built, not verified | Ollama provider tests pass; no live model call observed |
| 4 | Knowledge persistence | Verified | test_gitbrain.py memory rebuilds from Git |
| 5 | Agent creation | Verified | test_agentops.py proposal-to-accept path |
| 6 | Scheduled execution | Verified | test_runtime.py schedule/worker tests |
| 7 | Application creation | Verified | test_agentapps.py |
| 8 | Application modification | Verified | test_agentapps.py update/delete |
| 9 | Monitoring | Verified | dashboard state API; /api/v1 read views tested in test_apiv1.py |
| 10 | Governance | Verified | approval-gated actions in test_runtime.py, test_adapters.py |
| 11 | Theme customization | Verified | theme catalog + WCAG test |
| 12 | Cross-agent handoff | Verified | handoff export test in test_runtime.py |
| 13 | GitOps | Verified | test_gitops.py preview/apply/invalidation |
| 14 | Backup and restore | Verified | seed/backup paths in test_runtime.py, test_seed_local.py |
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
