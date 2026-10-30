# Friendly first-run guide for Mac

The local HQ needs Git and Python 3.10+. GitHub CLI is needed for the first push to your fork. Hermes and Codex are optional assistants; you can use the dashboard without either or a paid model account.

## Start locally

Follow [Fork → Clone → Bootstrap](FORK_START.md). Open Terminal in your cloned fork and run:

```bash
./bootstrap.sh
```

The script offers named installations for missing tools using an existing Homebrew installation, then seeds `AIHQ_HOME` (default `~/PersonalAIHQ`). It runs `./hq init` to initialize/reconcile declarations and the private database at `runtime/os.sqlite3` outside the repository, followed by read-only `./hq validate`, the health check and tests. No model is required. You may install the tools yourself; [Homebrew instructions](https://brew.sh/) are provided when needed. No remote installer is executed by the script, and no install happens without your affirmative answer. A Mac with the required tools can proceed without Homebrew.

`./setup.sh` is an alias for this same flow. `--check` reports tools without changes. `--yes` skips installation and login offers and uses installed tools; it does not authorize a push. GitHub sign-in and the literal **PUSH** confirmation are separate from local verification. See [fork guards and recovery](FORK_START.md#consent-and-retries).

After verification:

```bash
./hq start
```

Open http://127.0.0.1:8731. `./hq start` serves the browser interface; task processing is a separate process. If you want to process local tasks, open another Terminal in the checkout and run `./hq worker`, or `./hq worker --once` for one pass. Bootstrap does not start a worker. Dashboard approvals do not merge or deploy code. Stop each process with Control-C in its own terminal.

## Optional Hermes assistant

Install Hermes separately only when you want conversational assistance. Run `./hq hermes-plan` to print safe installation and local-model instructions without installing anything. The [official installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation) describes the macOS Desktop package. Review its source and normal macOS installation prompts. This bootstrap neither installs Hermes nor disables Gatekeeper or quarantine checks. If macOS blocks an app, stop and verify the package with the publisher.

After installing, reopen Terminal and check `hermes --help`. Desktop and CLI availability can differ; use the publisher's supported CLI setup if `hermes` is unavailable. The HQ remains usable while you resolve it.

### Use a local model

A hosted subscription is optional. Hermes supports local OpenAI-compatible model servers; see its [local-model FAQ](https://hermes-agent.nousresearch.com/docs/reference/faq#can-i-use-it-offline--with-local-models) and [model configuration guide](https://hermes-agent.nousresearch.com/docs/user-guide/configuring-models).

1. Separately install a local model runtime you trust, such as Ollama or llama.cpp. Choose and download a model that fits the Mac's available memory and supports tool use; downloads need network access and disk space. Bootstrap does not install a runtime or download models.
2. Start the runtime's API server bound to loopback only, load your chosen model and record its exact model identifier and supported context size.
3. Run `hermes model` and choose **Custom endpoint**. Enter the local OpenAI-compatible base URL, such as `http://127.0.0.1:11434/v1` for a default Ollama server. Use the model identifier actually loaded, the runtime's documented local authentication setting, and a context size supported by both Hermes and that runtime.
4. Verify the selected provider and model before chatting. Do not assume local inference also makes web tools, auxiliary models or integrations offline; review those separately before using private data.

A hosted provider is another choice: use `hermes model` and review its billing and data handling. The bootstrap does not select a provider or subscribe for you.

### Review skills before trusting

Read `AGENTS.md` and the bundled `.agents/skills/` files. If you approve them, consult `hermes skills --help` for your installed version's trust command and scope. Skill trust is a separate manual decision; setup never grants it automatically.

Then open a second Terminal in the checkout:

```bash
./hq chat
```

Hermes is a phone and terminal door onto the same apps: it asks a module app (`./hq app <id> ask "..."`) instead of answering from its own memory. There are no projects or record boards any more; apps are code under `apps/<id>` per `system/contracts/MODULES.md`.

## Optional Codex

Install and authenticate Codex separately if you want it as an implementation tool. It is not a requirement for local HQ, Hermes or first push. Review the tool's own installation and account instructions before installing; this bootstrap does not install it, log it in or grant it permissions.

## Quick problem solving

- Python missing or older than 3.10: install a supported Python, open a fresh Terminal and rerun bootstrap.
- GitHub CLI missing or signed out: local setup still completes; later run `gh auth login` and `./scripts/first-push.sh`.
- Push interrupted: rerun `./scripts/first-push.sh`; a saved onboarding commit is reused.
- Tests fail: bootstrap stops before the push. Save the test output and fix the failure before retrying.
- Hermes missing: the dashboard works; follow the optional assistant steps when ready.
- Dashboard port in use: stop your old dashboard or run `./hq start --port 8732`.
- Check tools without changes: `./setup.sh --check`. Run the health check with `./hq doctor` and the test suite with `./hq test`.

Private runtime state lives in `AIHQ_HOME` (default `~/PersonalAIHQ`), including `runtime/os.sqlite3`; portable declarations and any versioned project files live in the checkout. Back up both locations before migration. See [MAC_MINI.md](MAC_MINI.md) for a manual local service example and rollback.
