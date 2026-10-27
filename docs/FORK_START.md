# Fork, clone, bootstrap and first push

1. Sign into GitHub and ask the owner for access to [InfiniteArchOpenClaw](https://github.com/Rla102277/InfiniteArchOpenClaw).
2. Choose **Fork** and create the fork under **your own account**. Check that your username is in its URL. If private forking is restricted, stop and ask the owner for a shareable template.
3. Install [GitHub Desktop](https://desktop.github.com), sign in, then choose **Code → Open with GitHub Desktop** on your fork.
4. Clone into a folder you control. In GitHub Desktop choose **Repository → Open in Terminal** and run:

   ```bash
   ./bootstrap.sh
   ```

5. If Git or Python 3.10+ is missing, approve the named installation offer or install it yourself and rerun. Apple Command Line Tools can supply Git. Homebrew is only needed for Homebrew installation offers; an already configured Mac does not need it.
6. The bootstrap seeds a private workspace, runs `./hq init` to initialize/reconcile declarations, runs read-only `./hq validate`, then the local health check and test suite, and offers GitHub sign-in. **No model is required. Hermes, Codex and model subscriptions are optional** for local bootstrap and first push. Follow [ONBOARDING.md](ONBOARDING.md) when you want an assistant.
7. When GitHub CLI is signed in, the bootstrap validates your fork and asks you to type **PUSH**. This creates a generic onboarding commit and pushes the current branch to your fork. Review your branch's existing commits first: a Git push includes its unpublished history.
8. Refresh your fork on GitHub and look for `onboarding/FIRST_BOOTSTRAP.md`. Then run `./hq start` and open http://127.0.0.1:8731.

**Clone your fork for this flow; do not use Download ZIP.** A ZIP has no Git history or origin. Bootstrap never creates a repository or silently chooses a remote for it.

## Consent and retries

- `./bootstrap.sh --check` and `./setup.sh --check` only report installed tools.
- `./setup.sh` uses the same bootstrap flow, including the fork-only push offer.
- `./bootstrap.sh --yes` uses installed tools and skips installation/login offers. It still requires a separate literal **PUSH**; EOF or any other answer cancels.
- `./bootstrap.sh --launch` starts the dashboard after successful bootstrap.
- Every installation is named and requires an affirmative answer. No remote installer is downloaded and executed. No macOS security settings are changed. There are no automatic Codex logins, Hermes configuration changes or skill-trust actions.

If GitHub CLI or authentication is missing, local setup completes and first push is postponed. Install/sign in when ready, then run:

```bash
gh auth login
./scripts/first-push.sh
```

If reading the remote fails, no marker or commit is created; fix the connection and retry. If a push fails after committing, the marker commit stays local and the script exits with status 5. Rerun the same first-push command, approve **PUSH** again, and it reuses that commit. When the remote already matches, retry exits successfully without prompting or committing.

The guard requires one identical effective fetch/push URL on GitHub, an origin owned by the authenticated account, GitHub-confirmed fork status, a named branch and a clean working tree. It rejects alternate push destinations and multiple URLs. It pushes only that branch, without force, tags or recursive submodule pushes. Account/fork validation failures stop the push; resolve them rather than bypassing the guard.

## Rollback and handoff

Canceling the push or failing the connection check leaves the checkout clean. For a failed push with a saved commit, keep the commit and retry; do not delete the marker to restart. To undo an already published onboarding marker, use a reviewed revert through your normal GitHub workflow rather than rewriting branch history.

Stop the dashboard and any separate worker with Control-C in their respective terminals. Local bootstrap does not install services or start a worker. Keep a backup of `AIHQ_HOME` (default `~/PersonalAIHQ`), including `runtime/os.sqlite3`, before moving or removing it; reruns preserve existing seed files and reconcile declarations.

Private workspace validation runs before any seed files are written. A root inside the checkout, a symlink root or a symlink in a private seed path stops bootstrap before GitHub access, commit or push. Keep the root outside the checkout and use real private directories; inspect and back up existing data before changing a rejected path.

Maintainer acceptance criteria: bootstrap succeeds without Hermes/Codex/Homebrew when required tools exist; verification failures prevent push; fork and consent guards reject unsafe destinations; failed real Git pushes retry without another commit. Reproduce the owned checks with:

```bash
python3 -m unittest discover -s tests -p 'test_fork_push.py' -v
python3 -m unittest discover -s tests -p 'test_bootstrap.py' -v
python3 -m unittest discover -s tests -p 'test_seed_local.py' -v
bash -n bootstrap.sh setup.sh scripts/first-push.sh
git diff --check
```

Tests use temporary checkouts and bare Git repositories, fake GitHub responses and a mocked transport boundary. A real bare-repository hook rejects the first push to exercise recovery. Bootstrap integration runs the actual seed, init, validation, health check and local test commands with a controlled tool path and isolated home; its database stays outside the checkout and its fixture test suite excludes bootstrap tests to avoid recursion. Installers, browser launchers, service installation and security-changing commands are blocked.

No project `PROJECT.md` or `docs/HANDOFF.md` exists in the starter's empty `projects/` directory. For a created project, resume through [WORKFLOW.md](WORKFLOW.md), record verification in its handoff and obtain independent review and CI before merge/release. Offline tests do not establish live GitHub authentication, installer behavior, model quality or Mac mini service readiness.

Verification handoff (2026-10-26): observed 12 fork/push tests and 14 bootstrap tests passing, each command above exiting 0 after init/validate integration. Shell syntax, the repository's `scripts/verify.py` document/skill gate, whitespace checks and parsing the manual launchd XML also passed. The earlier bootstrap fixture failures came from a missing `cat` in its isolated tool path; the corrected fixture includes it. Only the eight assigned bootstrap/test/guide files were edited for this slice. No commit, branch, live push, installation or service load was performed in the working checkout. Full-suite/hosted CI and independent review remain separate gates for the integrating maintainer.

Private-path review follow-up: after the maintainer hardened seed validation, observed 17 bootstrap tests, 4 direct seed tests and 12 fork/push tests passing, each suite exiting 0. New bootstrap regressions reject a root inside the checkout, a symlink root and a projects symlink into the checkout before any seeded file, commit, GitHub call or push. Direct seed regressions cover root, child-directory, nested-directory and seed-file symlinks, preserving existing destinations and owner data. Read-only `./hq validate`, shell syntax and whitespace checks also passed. This follow-up changed only the two test files and this guide; the full suite and hosted CI were not rerun here.
