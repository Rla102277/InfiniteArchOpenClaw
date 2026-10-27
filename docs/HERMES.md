# Hermes and replaceable adapters

Hermes is the preferred conversational coordinator. Local apps, tasks, memory and scheduling work without it.

Use the [official installation guide](https://hermes-agent.nousresearch.com/docs/getting-started/installation) and the signed macOS Apple Silicon package. Follow normal macOS security prompts. This project never downloads/executes remote installers, removes quarantine flags or disables Gatekeeper. If macOS warns, verify the source and publisher before proceeding.

After installation, ensure the CLI is on PATH and run `hermes model`. Choose a provider interactively. Keep credentials in the tool's private configuration or credential store. Optional `hermes setup --portal` enables vendor services; it is not required here. Review `AGENTS.md` and `.agents/skills/` before trusting a particular repository revision. Reassess trust after updates. Bootstrap installs no third-party skills.

## Local inference

Run an installed Ollama server with a downloaded tool-capable model. In `hermes model`, select Custom Endpoint, `http://127.0.0.1:11434/v1`, no API key and the exact model name. Configure the server context to meet current Hermes requirements. The reviewed provider reference specifies at least 64,000 tokens for agentic tool use; context and model size both consume RAM. Measure memory fit and avoid assuming a large model fits. Keep cloud fallback disabled if inference must remain local.

Hermes Desktop also offers Local Models settings. Consult upstream [providers](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md), [local setup](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/local-ollama-setup.md) and [CLI reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md). The reviewed CLI documents `hermes chat --oneshot --max-turns 8 -q PROMPT`, used by this adapter. Verify `hermes chat --help` after upgrades.

Local inference does not restrict agent tools: web, messaging and terminal integrations may still use the network. Review tool permissions separately.

| Adapter | Working transport | Boundary |
| --- | --- | --- |
| Hermes | Installed one-shot CLI | Configured provider/native tool approvals; no OS sandbox added |
| Codex | Installed `codex exec` | Explicit read-only sandbox; provider login required |
| Claude | Installed `claude -p` | Plan mode, built-in tools disabled; review plugins/hooks separately |
| Ollama | Fixed loopback HTTP endpoint | Already running server/model; text generation only |
| Devin | Portable JSON handoff | No cloud submission or polling |

Presence is reported as `installed-unverified`: authentication and a successful provider call are not inferred. Tests use controlled fixtures; no paid/cloud model calls or Hermes installation were performed. Replace transports in `aihq/adapters.py` with corresponding authorization/failure tests. `agents/` records portable role intent and never expands execution permissions.
