# Move the brain without moving private data into Git

The same canonical declarations can be cloned onto macOS or Linux. No account name, absolute device path, credential, or personal example is required by the manifests. CI targets Python 3.12 and 3.14 on Ubuntu and macOS; a configured matrix is not proof that hosted CI ran.

## What travels

Clone the reviewed source revision with `system/`, `vault/`, `agents/`, `themes/`, `workflows/`, `apps/`, approved skills, and the runtime source. Markdown uses relative links and Obsidian wikilinks; Obsidian is optional. Keep machine-specific vault settings local.

Transfer private state separately through an owner-approved, protected channel. `AIHQ_HOME` or `~/PersonalAIHQ` contains `runtime/os.sqlite3`, private memory, local projects/documents, logs, and exports. Credentials remain in the platform credential store or a protected external secret store. Do not put a database, a backup archive, a raw prompt, or a private handoff packet into Git to make transfer convenient.

## New-machine sequence

1. Review the intended source revision and requirements before executing any setup. Installation and provider sign-in are explicit owner actions. This slice does not run installers.
2. Choose an absolute external `AIHQ_HOME`, outside and separate from the checkout. Inspect owner-only directory permissions. The application must reject an overlapping or symlink-escaped root.
3. Validate the checkout with `python3 scripts/verify.py` and `python3 -m aihq.cli validate`. Both are required; unavailable or failed validation blocks readiness. Inspect CLI help before invoking commands in an older checkout.
4. Initialize using `python3 -m aihq.cli init`, then inspect `python3 -m aihq.cli state`. Initialization and reconciliation may write private local state; review the proposed root first. Cloning alone does not restore private records.
5. Restore a consistent private backup into a new external root if needed. Reconcile definitions and inspect available apps. Restore credentials separately; no source checkout or handoff authorizes cloud access.
6. Run standard-library tests and a local preview using disposable private state. Verify board lifecycle, record persistence, restart, approval rejection, and local digest behavior with actual output.
7. Use `python3 -m aihq.cli worker --once` for a bounded local tick. Running a worker with `--allow-external` is a separate explicit decision requiring a configured provider and per-request approval. Devin remains handoff-only.

The runtime also exposes `action` with a JSON action object, `reconcile`, `handoff`, `backup --confirm`, and `hermes-plan`; consult `python3 -m aihq.cli --help` and subcommand help for the exact interface of the reviewed revision. Do not infer a shell command from task text or invoke an installer during a digest.

## Change and GitOps

Inspect the remote, branch, clean status, current revision, target revision, and proposed diff. `reconcile --pull` first provides the exact preview; only a later explicitly approved `reconcile --pull --confirm` may pull that same reviewed target using `git pull --ff-only`. A dirty checkout, divergence, changed preview, or stale approval must stop the update. See [GITOPS.md](../system/contracts/GITOPS.md). No automatic push, merge, skill trust, or production deployment follows reconciliation.

## Backup and rollback

Before changing a database schema or moving machines, preview the backup scope and explicitly approve `backup --confirm`. The command writes a consistent SQLite snapshot under the external root's `exports/` directory with owner-only file permissions. It does not include private Markdown documents, local project files, credentials, or an encryption layer. A raw copy of a live SQLite file may omit journaled changes. Protect the artifact and record the associated source revision outside Git; back up the other required private files separately.

Restore into a separate external directory, verify integrity and expected record counts, confirm access controls, and inspect the matching source revision. Switch `AIHQ_HOME` only after a successful rehearsal; retain the previous root. Restore credentials independently. For a failed source update, use the earlier revision in a clean checkout; use a reviewed revert to undo shared source history. Retain private records when disabling or reverting a generated app.

## Handoff and limits

Supply the reviewed revision, generic task scope, evidence, known limitations, and rollback using [HANDOFF.md](HANDOFF.md). Default handoff should exclude private state. `handoff --task` includes the selected private prompt; preview and redact it before any transfer. Destination agents and machines need fresh approvals and authentication.

Cloud services have not been live-tested, installers have not been run, no Mac mini has been provisioned, and no production deployment has been performed for this slice. Always-on startup, device recovery, remote access, encryption, and unattended operation need their own target-machine evidence. The declarative builder works within fixed record-board templates; arbitrary generated application code is future work.
