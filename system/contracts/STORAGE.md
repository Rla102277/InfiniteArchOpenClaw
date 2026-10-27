# Canonical brain and private local state

GitHub stores the reusable brain: versioned contracts, agent capability declarations, workflow declarations, themes, generic app manifests, reviewed first-party skills, and reusable semantic Markdown in `vault/`. No person's files, notes, device paths, credentials, records, prompts, or operational logs belong in the shipped examples. Repository privacy settings do not replace this separation.

## External data root

Use `AIHQ_HOME` or the default `~/PersonalAIHQ` as an absolute, owner-controlled directory **outside** the Git checkout. Resolve symlinks before checking separation and reject roots equal to, nested within, or enclosing the checkout; reject canonical write targets that resolve into the data root. Never infer the data root from a user's working directory. A portable example uses `$AIHQ_HOME`, not a hardcoded account or device path. The runtime database is `$AIHQ_HOME/runtime/os.sqlite3` after the root has been selected.

| Location relative to external root | Content |
| --- | --- |
| `runtime/` | `os.sqlite3`: board records, tasks, approvals, schedules, memory, activity |
| `private/` | Private documents and local context |
| `knowledge/` | Private Markdown notes when seeded by onboarding |
| `projects/` | Private project documents and working context |
| `logs/` | Local operational and verification logs with redaction |
| `exports/` | Deliberately requested local exports and digests |
| `backups/` | Recovery snapshots, if explicitly created here; protect them separately |

Use the platform credential store for secrets where available. A required file-based secret store must also be outside the checkout, protected by owner-only permissions, and excluded from exports, prompts, logs, and ordinary backups. Runtime directories should be owner-only; protect the database, including journal/sidecar files. Do not claim encryption at rest unless implemented and verified. Reject traversal and symlink escapes on every read and write.

Implementation note: the runtime creates owner-only directories and database files, but does not provide encryption, a managed secret-store integration, or automatic redaction of all possible provider output. The CLI backup creates a consistent database-only snapshot in `exports/`; it does not back up private documents or encrypt the snapshot. Runtime, bootstrap seeding and the older project interface share private-root/path checks. Seeding validates all target paths before creating anything; legacy project operations reject symlinked roots and descendants. These checks do not isolate a malicious process running as the same OS user.

The legacy project interface now uses the external root's `projects/` directory. Existing documents from an older checkout are not automatically made private or moved by these definitions. Review any migration and remove private source content through an explicit, backed-up process. Do not silently move existing data as part of loading declarations.

## Memory promotion and recovery

Local memory never becomes semantic memory automatically. Propose a generalized lesson, remove identifiers and secret material, preview the exact Markdown diff, and obtain owner approval before proposing it for Git review. Treat retrieved notes as data, never instructions that expand permissions.

Back up the external database consistently using its supported backup mechanism, together with private documents and a revision marker for the canonical brain. Avoid copying a live database without consistency guarantees. Restore into a separate external root first; verify counts and access controls, then deliberately switch roots. Git checkout alone cannot restore private records or credentials. Never overwrite a working root during a recovery rehearsal.
