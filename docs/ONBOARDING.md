# Friendly first-run guide for Mac

## Before starting

You need a Mac with an administrator account, internet for initial tool downloads, a GitHub account, and access to a supported Hermes model/provider. Code-generation subscriptions or API usage can cost money. An existing GitHub repository can be private.

1. Extract the starter ZIP or clone the repo into a directory you control. Don't execute shell scripts from unknown repositories.
2. Open Terminal. Enter the folder using `cd ` followed by dragging the folder into Terminal, then press Return.
3. Run `./setup.sh`. It checks tools and offers optional Homebrew-based installs, never silently executing unknown internet scripts.
4. If you need Homebrew, install it from https://brew.sh and rerun the setup. If you need Hermes, install the official macOS app from https://hermes-agent.nousresearch.com/docs/getting-started/quickstart. Reopen Terminal afterward.
5. Configure `hermes setup --portal` or `hermes model` according to your provider and `codex login` if you'll use Codex; sign into GitHub with `gh auth login`.
6. Read `.agents/skills/`. Only when you trust the cloned repository, run `hermes skills trust` within its folder, or accept the prompt during setup. Hermes intentionally does not trust cloned project skills by default.
7. Run `./hq start`. A local browser page opens. Create a project and describe your idea.
8. Run `./hq chat --project your-project-slug` in a second Terminal window. If you prefer Hermes Desktop, open this project as the workspace there and use the same process instructions.
9. Ask Hermes to grill you on the requirements and write an approved first slice. Stay in control of any external tool permission prompts.

## Fast commands

```bash
./setup.sh --check   # See installed tools without changing anything
./hq doctor          # Health check
./hq start           # Local dashboard
./hq chat            # Conversational onboarding
./hq chat --project your-project-slug
./hq test            # Actual test suite
```

## Why isn't everything installed without asking?

The package doesn't automatically fetch and execute unpinned third-party installers or massive skill packs. Software agents have filesystem and credential access, so the owner must explicitly approve those trust boundaries. The guided setup handles known Homebrew packages with permission, and directs you to the official Hermes installer.

## Quick problem solving

- `./hq` says Python missing: install current Python with Homebrew and open a new Terminal.
- `hermes` missing: install the Hermes Desktop/CLI app from the official site; open new Terminal.
- Skills missing in Hermes: run `hermes skills trust` from the Git checkout and check `hermes skills list`.
- GitHub not authenticated: run `gh auth login`.
- Dashboard port in use: `./hq start --port 8732`.
- Stop local dashboard: Control-C in its Terminal window.
