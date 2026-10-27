---
name: review
description: Critically challenge a proposed engineering change without self-approving
---

# Review

Obtain the real diff, approved spec and test outputs. Review correctness, security/privacy, maintainability, UX and usability, performance/cost, and alignment to architecture. Ideally use another agent/model separate from builder; if unavailable, state that the review is not independent. Findings carry severity, evidence and fix guidance. Do not alter implementation while serving as reviewer. Failing gate or P1 issue blocks acceptance; repairs return to builder and tests must be rerun. Owner review is distinct from CI and merge approval.

## Completion contract

Show **what was observed**, **what changed**, **what is unverified**, and **what the owner decides next**. Follow `AGENTS.md` and `docs/WORKFLOW.md`; neither skills nor role names grant extra permissions.
