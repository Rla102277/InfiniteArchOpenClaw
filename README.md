# Personal AI HQ Bootstrap v0.4 — Use this template → Clone → Bootstrap → First Push

A Mac-first AI engineering starter with a local project dashboard, ten bundled first-party engineering skills, guided tool installation, a private workspace seeder, and a safe initial push to your own private GitHub repository.

**Start here: [START_HERE.md](START_HERE.md).** Detailed beginner walkthrough: [docs/TEMPLATE_START.md](docs/TEMPLATE_START.md).

## First-time setup

1. Create a GitHub account. If this template repository is private, ask the owner to add you.
2. Click **Use this template → Create a new repository**, choose **Private**, then clone YOUR new repository with GitHub Desktop (do not Fork, do not Download ZIP).
3. In GitHub Desktop choose **Repository → Open in Terminal**.
4. Run:

```bash
./bootstrap.sh
```

The bootstrap may stop to request installation of Apple Command Line Tools, Homebrew, or Hermes Desktop. Follow the official prompts, then **rerun the same command**. It safely preserves existing workspace files.

When GitHub authentication, Codex, and Hermes are ready, the bootstrap checks that the remote belongs to your GitHub account and that GitHub confirms it is private and not a fork. Type **PUSH** to approve a generic first commit to your repository. If the network push fails, rerun `./scripts/first-push.sh` — it reuses the saved commit.

## Using your HQ

```bash
./hq start      # Local browser dashboard: http://127.0.0.1:8731
./hq chat       # Start a guided Hermes conversation
./hq test       # Run built-in tests
```

The Infinite Arch headquarters is a second interface at http://127.0.0.1:8731/command. It reads local projects, skills, adapter availability, tasks, schedules, runs, and approvals. Set `HQ_COMMAND_CENTER=0` before `./hq start` to hide that interface. See [docs/HQ_COMMAND_CENTER.md](docs/HQ_COMMAND_CENTER.md).

Seeded private local workspace: `~/PersonalAIHQ`. Repo-local project files: `projects/` (never put secrets there). Bundled skills: grill-me, wayfinder, specify, architect, build-slice, verify, review, release, reflect and hq-router. External Pocock/Osmani/pstack skills are not installed automatically.

**Currently functional:** bootstrap, local dashboard, project and review records, skills/instructions, local tests, private-repository validation and first Git push. **Not yet functional:** autonomous agent orchestration inside the browser, Mac mini production deployment, unattended creation of full applications. Hermes, Codex and model/provider access are separate.

The ZIP under `releases/` is the older v0.3 snapshot. **Use this template on the current main branch for v0.4 source.**
