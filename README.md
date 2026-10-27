# Personal AI HQ Bootstrap v0.2

A repeatable Mac starter to seed a local Personal AI HQ and begin a
conversation-driven engineering project. Start with [START_HERE.md](START_HERE.md).

## One command after cloning

```bash
./bootstrap.sh
```

It checks Mac prerequisites, offers tool installation and sign-in, seeds
`~/PersonalAIHQ`, verifies the starter, and tells you how to launch.

Use `./bootstrap.sh --check` to view available tools without installing anything.
Use `./bootstrap.sh --launch` to also start the local dashboard after setup.

The local HQ app, first-party skills, CI, and review policy are in this repo.
External provider binaries and third-party skills are **not** redistributed;
setup installs or guides official installs with explicit approval.

## Publish as a template

When authenticated to GitHub, run:

```bash
./scripts/publish-private.sh personal-ai-hq
```

This creates a new private repo and pushes this code after confirmation.
For a reusable GitHub template, enable Template repository under repo settings.
Personal runtime data remains under `~/PersonalAIHQ` and is not pushed by default.

## Limits

This release scaffolds the environment and supports CLI conversations, not yet
one-click autonomous engineering from the dashboard. Verify tool/provider
compatibility on a real Mac before using it with other people.

## Preferred friend workflow: Fork → Clone → Bootstrap → First push

See [docs/FORK_START.md](docs/FORK_START.md). Once they fork this repository
into their account and clone **their own fork** to the Mac, `./bootstrap.sh`
seeds their private `~/PersonalAIHQ` data and invokes `scripts/first-push.sh`.
It checks GitHub login, verifies that `origin` belongs to the signed-in account
and is actually a fork, then asks the person to type `PUSH` before creating
and pushing one generic initial onboarding commit. No secrets or personal
knowledge are uploaded. A cloned fork is required; a downloaded ZIP is not enough.

## Publishing to InfiniteArch (repository owner)

`./scripts/publish-infinitearch.sh` authenticates to GitHub as `Rla102277`,
clones `Rla102277/InfiniteArchOpenClaw`, copies the starter, generates a ZIP in
`releases/`, shows the files, and waits for `PUBLISH` before pushing. It refuses
when starter files already exist upstream, to avoid overwriting prior work.
Then friends fork the upstream repository, clone **their fork**, run
`./bootstrap.sh`, and confirm the first fork push.
