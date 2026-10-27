# Start Here — One-command Mac installation

This repo packages the HQ web dashboard, the first-party engineering skills,
workflow policies, tests, and a local workspace seeder. It installs external
CLI tools when you approve, using official installers. Providers still need
individual logins. No API secrets or external skills are bundled.

**On a Mac:** open Terminal, clone this repository (or extract the ZIP), `cd`
into it, then run:

```bash
./bootstrap.sh
```

The bootstrap checks Apple's developer tools and Homebrew, offers to install
Git/Python/GitHub CLI/Codex, points you to the signed Hermes Desktop installer,
seeds `~/PersonalAIHQ/`, offers authentication and local skill trust, initializes
Git, and runs the tests. It is safe to rerun and never overwrites your local
workspace files. MacOS dialogs and provider sign-in may require intervention.

```bash
./hq start          # browser-based local project dashboard
./hq chat           # Hermes conversation with repository rules
./scripts/publish-private.sh my-ai-hq  # OPTIONAL: push to a NEW private GitHub repo
```

**Security:** Read files and scripts before trusting or running them. This
bootstrap does not install unreviewed external skills or silently run
remote shell scripts. The bundled skills are original starter workflows,
not copies of Pocock/Osmani/pstack. Third-party integrations are optional.

**What is real in v0.2:** local dashboard, project records, approval recording,
Mac tooling bootstrap, workspace seeding, Hermes CLI handoff, CI tests.
**Not yet automatic:** embedded chat UI with live Hermes/Codex jobs, unattended
full app builds from the dashboard, cloud deployment, and Mac mini service setup.
