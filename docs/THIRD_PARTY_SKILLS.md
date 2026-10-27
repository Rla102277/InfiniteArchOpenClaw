# Skills provenance and optional integrations

The following external projects inspired the workflow design; **none are vendored, installed, or invoked automatically**. The included skills are original minimalist patterns, not verbatim copies.

| Source | Address | What to inspect before adopting |
| --- | --- | --- |
| Matt Pocock | https://github.com/mattpocock/skills | grill-me, wayfinder, context and ticket structures |
| Addy Osmani | https://github.com/addyosmani/agent-skills | interviewing, spec-driven development, testing, review, security |
| pstack portable | https://github.com/backnotprop/pstack | architect, interrogate, arena, show-me-your-work, reflect |
| Hermes | https://github.com/NousResearch/hermes-agent | project skill trust, CLI, memory, provider setup, skills scanner |
| Codex | https://github.com/openai/codex | CLI auth, approvals and execution boundaries |

## Approved dependency manifest (initial)

There are no third-party skills preinstalled. Before adding a third-party skill: record the upstream URL, exact immutable commit SHA, license, skill path, dependencies, test outcomes, approver and date here. Prefer a GitHub PR with reviewed diffs instead of one-line installers from unknown sources.

To browse: `hermes skills search <term>` followed by `hermes skills inspect <source/path>`. The Hermes hub performs a security scan, but automated scanners do not guarantee safety.
