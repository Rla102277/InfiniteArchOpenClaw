---
type: pattern
scope: public-reusable
---

# Daily Workflow

Start from the latest private [[Handoff]], check the current revision and open decisions, then choose one useful next action. [[Generic Project]] provides a small planning pattern. Keep actual priorities and records in private local storage, following [[Memory]].

The generic daily digest runs only the deterministic local `digest.create` operation with an empty payload. A declaration does not enable a timer. Scheduling requires an explicitly approved local setting and evidence that the runner implements the required behavior.

A digest can summarize approved local records and pending decisions. It cannot launch a model, delegate work, approve an action, pull from GitHub, or send a message. Consult [the scheduler contract](../system/contracts/SCHEDULER.md) and [[Governance]] before changing behavior.
