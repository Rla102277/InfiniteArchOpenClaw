# InfiniteArch Agentic OS handoff

## Start here

Any future AI or human maintainer should first read `AGENTS.md`, `START_HERE.md`, `docs/WORKFLOW.md`, this handoff, the architecture, runtime, security, and capability guides, then the active project's local documents. Inspect the actual checkout revision, working changes, and CI before continuing. Preserve existing work and distinguish observations from proposals.

The intended product is a portable GitHub canonical brain with a private local operating workspace, reusable memory, replaceable agents, deterministic local automation, and a bounded app builder. Resume from the owner's latest approved objective and acceptance criteria. Repository workflow requires independent review, owner demo acceptance, and human-controlled merge/release after verification. Approval of a demo or handoff never authorizes installation, cloud execution, publication, or deployment by itself.

## Changes

The source now defines `InfiniteArch Agentic OS` 1.0.0, default Hermes/Forest, Hermes coordinator, Codex/Claude implementers, Devin handoff-only implementer, Forest/Paper/Midnight themes, the local daily digest, and exactly three generic app seeds. No private records or machine-specific examples are included. The Obsidian-compatible vault links reusable definitions and keeps private memory external.

The builder output contract includes `apps/<id>/app.json`, `SPEC.md`, and `HANDOFF.md`; these are declarations and documentation only. Runtime data uses `AIHQ_HOME` or `~/PersonalAIHQ`, with SQLite at `runtime/os.sqlite3` and local project documents under `projects/` in that external root.

## Verification ledger

Observed action tag resolution through `git ls-remote`:

| Action | Resolved major tag | Commit |
| --- | --- | --- |
| `actions/checkout` | `v4` | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-python` | `v5` | `a26af69be951a213d495a4c3e4e4022e16d87065` |

These full commits are pinned in the read-only CI workflow. CI targets Ubuntu/macOS and Python 3.12/3.14. `python3 -m aihq.cli validate` is an unconditional required check. Read hosted results from GitHub checks for the exact pushed revision; workflow configuration alone is not passing evidence. The published PR carries the current source revision and check links.

Local verification results must be recorded below only after observing commands and exit codes. Pending checks are not passing evidence.

| Check | Observed result |
| --- | --- |
| Exact seed schemas, IDs, skill references, links, private-path scan | Observed exit 0; 21 Markdown files and 57 links checked; no account/device paths found |
| Shell syntax for each script | Observed exit 0; each top-level/script shell file checked individually |
| `python3 scripts/verify.py` | Observed exit 0; required process files and ten skills checked |
| `python3 -m aihq.cli validate` | Observed exit 0; 3 apps, 3 themes, 1 workflow, 4 agents |
| `python3 -m unittest discover -s tests -v` | Observed exit 0: 134 tests, 45.976 seconds, Python 3.15.1 on macOS; earlier 108-test integration revision passed Python 3.12.12/3.14.7; hosted CI checks the final revision on both |
| Browser verification | Observed interview preview without writes, saved theme/preferences, app install/enable, retained record after process restart, completed local digest and memory update |
| Independent review | Six findings identified and addressed with regression coverage: launch context, private legacy paths, descendant cleanup, approval details, branch-bound pull, and seed prevalidation; review is not release approval |

## Known limitations and next gates

Cloud services have not been live-tested; installers have not been run; a Mac mini has not been provisioned; no production deployment has occurred. The declarative app builder is a bounded local feature; arbitrary generated application code is not supported yet. Permission metadata is not an OS sandbox. Strict rejection of every unknown/duplicate manifest key, cryptographic builder preview binding and approval expiry are not implemented. Builder publication now stages a complete bundle and atomically renames it into the catalog; private-state reconciliation follows separately. Owner-controlled executables/configuration are trusted: launch-time binding checks are not a defense against a malicious process running as the same OS user.

Next, confirm the current source revision and open decisions, run the available checks, inspect independent review findings, and demonstrate the local builder and retained records to the owner. Use `init`, `state`, JSON `action`, `validate`, `worker --once`, `reconcile`, `handoff`, `backup`, and `hermes-plan` through `python3 -m aihq.cli`; consult subcommand help before changing state. External jobs require both per-request approval and a separately enabled `--allow-external` worker. Keep hosted CI, provider authentication, recovery drills, and release approval explicitly outstanding until observed.

## Rollback and transfer

Record source revisions and a consistent private backup before any update or migration. Recover source in a separate clean checkout, or submit a reviewed revert for shared history. Restore private SQLite/documents to a separate external root and verify before switching. Reverting an app declaration must not delete private records. See [PORTABILITY.md](PORTABILITY.md).

Default handoff carries reusable context without private state. Selecting `handoff --task` adds the selected private prompt; inspect and redact before sharing. Approvals, credentials, provider sessions, and skill trust never transfer automatically. Use [the adapter contract](../system/contracts/ADAPTERS.md) to change implementers without expanding authority.
