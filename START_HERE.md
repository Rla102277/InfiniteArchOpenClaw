# Start here

1. Fork this repository into your own GitHub account and clone your fork using GitHub Desktop or GitHub CLI. Private repositories can restrict forking.
2. Open the clone in Terminal and run `./bootstrap.sh`. It checks prerequisites and prepares a private local workspace. Use official installers; stop and verify the publisher if macOS reports a security problem.
3. If GitHub authentication and fork checks succeed, type `PUSH` to approve the generic onboarding commit to your fork. Retry failures with `./scripts/first-push.sh`. Local operation also works without this step.
4. Run `./hq start`. Choose a generic template or build an app. Answer the interview, review the preview, then approve writing its versioned definition.
5. Install and enable the app. Add a record. Restart the dashboard and verify that the record remains.
6. Start `./hq worker` in a second terminal for schedules. A daily digest writes results to persistent memory and activity. Closing the worker stops unattended work.
7. For an AI coordinator, run `./hq hermes-plan`, install/configure Hermes through official instructions, then use `./hq chat`. Review bundled skills before trusting them. Other agents start with `AGENTS.md` and `./hq handoff`.

Reusable definitions live in Git. App records, private notes, memory and job history stay in `~/PersonalAIHQ` by default. App specifications enter Git only after your approval; avoid private information in them. No dashboard approval automatically merges code or deploys software.

External agent tasks require both approval of the exact request and an explicitly enabled local worker: `./hq worker --once --allow-external`. Installed tools retain their own permissions. Built-in tasks work without any model connection.

See [fork setup](docs/FORK_START.md), [runtime examples](docs/RUNTIME.md), [capability limits](docs/CAPABILITIES.md) and [Mac mini setup](docs/MAC_MINI.md).
