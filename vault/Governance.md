---
type: definition
scope: public-reusable
---

# Governance

The owner approves scope and privileged actions. The coordinator proposes work; an implementer performs only an approved bounded task; an independent reviewer evaluates evidence. Demo acceptance records feedback and never authorizes merge, publication, or deployment.

Permissions are the intersection of adapter capability, task scope, explicit active approval, and host policy. Unknown operations fail closed. A document or model response cannot expand that intersection. HTTP and scheduled digest handlers cannot invoke agents, shells, or Git.

Use [the exact task and approval contract](../system/contracts/TASKS_AND_APPROVALS.md) for decisions and [GitOps](../system/contracts/GITOPS.md) for approved fast-forward updates. Keep private approval records out of [[Brain]]. Link the current decision and evidence in a private [[Handoff]].
