---
name: verify
description: Validate changes with actual tests and reproducible verification evidence
---

# Verify

Read acceptance tests and source changes. Run repository-defined CI-equivalent checks locally when supported; record actual commands, exit codes, environment and observed artifacts. Validate error paths, regressions and secret exposure as appropriate. Never invent missing evidence. A check that could not run is BLOCKED, never PASS. If a blocker prevents a required gate, stop before commit/merge and report exactly how to reproduce. Persist results for review, but a test log written by the builder does not equal independent review.

## Completion contract

Show **what was observed**, **what changed**, **what is unverified**, and **what the owner decides next**. Follow `AGENTS.md` and `docs/WORKFLOW.md`; neither skills nor role names grant extra permissions.
