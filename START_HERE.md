# Start Here — Personal AI HQ v0.4

**No coding experience required.** This guide is for someone starting on their own Mac.

1. Make an account at https://github.com/signup and enable two-factor authentication.
2. Open https://github.com/Rla102277/InfiniteArchOpenClaw (the owner must grant access; private forking may be restricted).
3. Select **Fork** → create a copy under **your own account**. Verify YOUR username is in the URL.
4. Install/sign into **GitHub Desktop** from https://desktop.github.com.
5. On your new fork click **Code → Open with GitHub Desktop → Clone**.
6. In GitHub Desktop choose **Repository → Open in Terminal**.
7. Run exactly:

```bash
./bootstrap.sh
```

The installer checks for Apple Command Line Tools and Homebrew, offers the Git/Python/GitHub CLI/Codex tools, points to the official Hermes Desktop installer, seeds a local private workspace, checks and tests HQ, and offers login/trust steps. If you must finish a Mac/website installation, rerun the same bootstrap command.

When tools and GitHub authentication are ready, the installer confirms the GitHub repository belongs to YOU and is a fork. Type **PUSH** to authorize the first onboarding commit to **your fork only**. On a failed push, retry `./scripts/first-push.sh` — no duplicate commit.

Start the web HQ with `./hq start`, create a project, then run `./hq chat` in another Terminal window (or use the project-specific command shown in HQ). Tell Hermes: *“I'm not technical. Grill me with one question at a time, write the first plan, and wait for my approval before coding.”*

Your browser HQ is at http://127.0.0.1:8731. Its approvals do not merge code or deploy apps. Mac mini service install, web-embedded agent execution and automatic full-app coding are not part of this version.

**More details:** [docs/FORK_START.md](docs/FORK_START.md).
