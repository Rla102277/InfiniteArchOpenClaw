# Security, trust and governance

## Current boundary

The HQ development server binds to `127.0.0.1`, validates host and origin, uses an ephemeral anti-CSRF token for write endpoints, has no process-execution HTTP routes, and does not collect API keys. It is a single-user local preview; other processes running as your macOS user can access local files. Do NOT use port forwarding or publish it publicly.

## Agents and credentials

Never put access tokens in prompts, Markdown, `.env` committed to Git, URLs, or screenshots. Prefer vendor authentication tools and macOS Keychain. GitHub permissions should be scoped. Never configure unreviewed shell hooks or autonomous destructive commands. Keep development and production accounts distinct.

## Skills supply chain

The starter bundles ten original plain-text skills. Hermes project skill trust is a conscious user action; a trusted repo's skills can still change on `git pull`, so review updates. Third-party skills are **not** installed by default. Inspect repository, pinned commit, dependencies, shell commands, network access and risk before installation. Skills are not security boundaries; OS-level execution sandboxing is necessary for untrusted tooling.

## Human approval

HQ `Accept demo` means only that the owner accepted a recorded demonstration. It does not prove CI passed, authorize financial actions, merge, publish a skill, send communications or deploy code. The owner must independently approve those actions through the relevant tool.

## Data policy

Project ideas, features, decisions, review requests, and activity logs are written to `projects/`, which is meant to be version-controlled. These may contain sensitive business information: use **private** GitHub repositories where appropriate. Do not place personal secrets or regulated records in project files. Be aware that configured external AI model providers may process submitted prompts/code.

## Production hardening still required

Authenticated remote UI, device pairing, encryption/secret management, network authorization, job isolation, audit retention, resource budgets, backups and disaster recovery, signed release artifacts and rollback testing are NOT part of the initial local preview.
