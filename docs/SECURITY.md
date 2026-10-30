# Security, trust and governance

## Current boundary

The server binds to `127.0.0.1`, validates host/origin and cross-site fetch metadata, uses a per-process anti-CSRF token for writes, and collects no API keys. HTTP routes execute allowlisted local database operations only; even approved external tasks cannot run through a browser worker tick. There is no remote authentication. Other processes running as your user can access the same files and loopback API. Do not forward ports or expose the service publicly.

## Agents and credentials

Never put access tokens in prompts, Markdown, `.env` committed to Git, URLs, or screenshots. Prefer vendor authentication tools and macOS Keychain. GitHub permissions should be scoped. Never configure unreviewed shell hooks or autonomous destructive commands. Keep development and production accounts distinct.

## Skills supply chain

The starter bundles ten original plain-text skills. Hermes project skill trust is a conscious user action; a trusted repo's skills can still change on `git pull`, so review updates. Third-party skills are **not** installed by default. Inspect repository, pinned commit, dependencies, shell commands, network access and risk before installation. Skills are not security boundaries; OS-level execution sandboxing is necessary for untrusted tooling.

## Human approval

Legacy demo acceptance records feedback only. Runtime external-task approval authorizes one payload bound to the brain fingerprint, Git revision and executable hash. A separate CLI worker must opt into external execution. It does not prove CI, merge code, publish skills or deploy software. Native provider configuration/hooks can change independently and are not sandboxed by this runtime; review them before approval. Hermes retains native tools/approvals, Codex uses read-only sandbox, Claude disables built-in tools in plan mode, and direct Ollama uses a fixed loopback endpoint. Do not grant external execution to untrusted content merely because it is in Markdown.

## Data policy

Private app records, tasks, prompts/results, memory, events and legacy project notes live under `AIHQ_HOME` outside the checkout. Directories are owner-only and the SQLite file is mode 0600. Generated app specifications are intentionally written to Git only after explicit builder approval; keep those generic. Credential entry is delegated to each vendor's local configuration/Keychain. Secret-looking filenames are ignored as defense in depth, not as a content scanner. Review every diff before sharing; external providers may process approved prompts/code.

Reconciliation never executes manifest hooks. Unknown app permissions, workflow operations or theme CSS values fail closed. Changed app definitions require reinstall; removed definitions retain private records. A pending approval becomes stale if the brain/revision/executable changes. External timeouts stop process groups; interrupted work can have an unknown outcome and is not retried automatically.

Bootstrap never executes remote installer scripts, strips quarantine or bypasses macOS security warnings. Use signed official packages and verify publishers. First-push is fork-only with explicit typed approval and remote checks. GitOps pulls require an earlier exact preview and clean fast-forward; Git hooks are disabled for that pull operation. It does not auto-run downloaded runtime code or tests.

## Production hardening still required

Authenticated remote UI, device pairing, encryption/secret management, network authorization, job isolation, audit retention, resource budgets, backups and disaster recovery, signed release artifacts and rollback testing are NOT part of the initial local preview.
