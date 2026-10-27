# GitHub canonical brain and explicit updates

GitHub is the canonical versioned source for reusable definitions. Local databases, private memory, secrets, and logs remain outside the checkout. Git synchronization is not application deployment, private-state synchronization, or skill trust.

## Preview and approve

Use a human-controlled terminal session to inspect status, current revision, configured remote, tracked branch, and incoming changes. Fetching may update Git metadata but must not change the worktree; it must be an intentional action against the reviewed remote. Never pull during an HTTP request or timer tick. Never accept a remote, branch, hook, or executable from untrusted note content.

Show the owner the remote, branch, current and target revisions, changed file summary, schema/skill changes, and rollback procedure. Require explicit approval for that exact update. A dirty checkout, diverged history, unexpected remote, moving target revision, unsupported schema, or unavailable approval blocks the pull.

The required update policy is **`git pull --ff-only` semantics** against the reviewed remote and branch, bound to an approved full commit ID. The implemented CLI uses `fetch --no-tags origin` followed by `merge --ff-only <reviewed-commit>`, keeping the applied commit equal to the preview. `reconcile --pull` stores a signature over the full local branch ref, current revision, target revision, upstream, origin, and change summary in private state. Confirmation fetches again and rejects a changed signature; branch, HEAD and cleanliness are checked again immediately before merge. Origin must be a canonical `github.com` URL and the branch must track `origin/`. Fetch and merge disable hooks with `core.hooksPath=/dev/null`.

The preview includes a change summary, not a reviewed full diff: the owner still must inspect `git diff HEAD..@{u}` before confirmation. Current code does not separately reject every incoming executable or submodule change, sandbox updated code, or verify signatures. Do not execute newly fetched skills or code until they have been reviewed. There is no signed/expiring approval record for the pull plan, and a post-update validation failure does not automatically undo the worktree update.

No automatic merge, rebase, force reset, push, branch protection bypass, skill trust, installation, or deployment is part of this operation. Run declaration validation and the required checks after updating; an approved pull does not make newly changed skills trusted.

## Change, rollback, handoff

Changes need a bounded specification, explicit file ownership, reviewed diff, actual verification evidence, independent review when available, and owner acceptance. A separate authorized maintainer controls commits, branches, pushes, and merges; this contract does not authorize them implicitly.

Record the pre-update revision and external backup reference. If a change fails validation, stop use of the updated behavior and recover the previous revision in a separate clean checkout; retain the failed checkout and private data for diagnosis. Shared-history reversal uses a reviewed revert, never rewritten history. A data migration requires its own tested restore path before approval. Hand off the exact revisions, commands/exit codes, remaining gates, and next decision using [HANDOFF.md](../../docs/HANDOFF.md).
