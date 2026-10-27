---
name: release
description: Prepare a reversible human-approved deployment without silently publishing
---

# Release

Review target environment, threat model, data/credential boundaries, migrations, backups, observability, rollback, CI reports, reviewer findings and owner acceptance. Produce an explicit release checklist and ask separate human permission before pushing, merging, deploying, enabling schedules, sending notifications or spending money. This v0.1 starter contains no automated Mac mini production deployment, so provide a truthful manual gate and do NOT pretend to release. If evidence is missing, mark blocked and stop.

## Completion contract

Show **what was observed**, **what changed**, **what is unverified**, and **what the owner decides next**. Follow `AGENTS.md` and `docs/WORKFLOW.md`; neither skills nor role names grant extra permissions.
