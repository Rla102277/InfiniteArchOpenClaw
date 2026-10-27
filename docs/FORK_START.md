# Fork, clone, bootstrap and first push

**Use GitHub Desktop; don't download the source ZIP.** ZIP downloads have no remote Git history and cannot perform the first-push flow.

1. Sign into GitHub. Ask the owner for private repository access.
2. Go to https://github.com/Rla102277/InfiniteArchOpenClaw and choose **Fork** → YOUR account. If a private fork is disallowed, stop and ask the owner for a shareable public template. Do not change the upstream repository.
3. Install GitHub Desktop from https://desktop.github.com and sign in.
4. On YOUR fork, click **Code → Open with GitHub Desktop** and clone into Documents.
5. In GitHub Desktop, choose **Repository → Open in Terminal**. Then run `./bootstrap.sh`.
6. If macOS prompts for Apple Command Line Tools, complete that installation and rerun. If Homebrew is missing, open https://brew.sh when asked and follow official instructions, then rerun. If Hermes is missing, install Hermes Desktop from https://hermes-agent.nousresearch.com/docs/getting-started/installation, reopen Terminal and rerun.
7. The script seeds `~/PersonalAIHQ`, verifies the local app, offers model/GitHub/Codex sign-in and skill trust, and when all required tools are present calls the guarded first-push script.
8. Type `PUSH` when you authorize the first commit to YOUR fork. Refresh your fork on GitHub and look for `onboarding/FIRST_BOOTSTRAP.md`. If a network issue interrupts the push, rerun `./scripts/first-push.sh`.
9. Run `./hq start`, open http://127.0.0.1:8731, create a project. Open another Terminal window in the same repo and run `./hq chat` for the conversational engineering process.

The first-push script checks authenticated account ownership, fork status, a canonical GitHub remote, a clean working tree, and an explicit PUSH authorization. It does not push to the upstream account. Personal notes and secrets remain in local private storage unless you intentionally export them.

**Important limits:** The local dashboard works, but its chat is not yet integrated directly with Hermes/Codex execution. Mac mini deployment is future work. First-run installations require real user interaction and may incur paid AI-provider costs. Treat installed scripts and third-party skills as executable software and review the trust prompt.
