#!/usr/bin/env bash
# Interactive, idempotent setup. Does not silently run remote install scripts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ask() {
  local answer
  if [[ ! -t 0 ]]; then return 1; fi
  printf '\n%s [y/N]: ' "$1"
  IFS= read -r answer || true
  [[ "$answer" == y || "$answer" == Y || "$answer" == yes || "$answer" == YES ]]
}
status() { if command -v "$1" >/dev/null 2>&1; then printf '[OK]      %s\n' "$1"; else printf '[MISSING] %s\n' "$1"; fi; }

printf '\n╔════════════════════════════════════════╗\n║      PERSONAL AI HQ — MAC SETUP        ║\n╚════════════════════════════════════════╝\n\n'
if [[ "$(uname -s)" != "Darwin" ]]; then
  printf 'Setup installer currently targets macOS. The dashboard works with Python 3.10+ elsewhere.\n'
  [[ "${1:-}" == "--check" ]] || exit 2
fi
for command_name in git brew python3 gh codex hermes; do status "$command_name"; done
if [[ "${1:-}" == "--check" ]]; then exit 0; fi

if ! command -v brew >/dev/null 2>&1; then
  printf '\nHomebrew is not installed. Install it from https://brew.sh/ and rerun ./setup.sh.\n'
  printf 'This script will not run an unreviewed Homebrew installer for you.\n'
fi
if command -v brew >/dev/null 2>&1; then
  if ! command -v git >/dev/null 2>&1 && ask 'Install git using Homebrew?'; then brew install git; fi
  if ! command -v python3 >/dev/null 2>&1 && ask 'Install Python using Homebrew?'; then brew install python; fi
  if ! command -v gh >/dev/null 2>&1 && ask 'Install GitHub CLI using Homebrew?'; then brew install gh; fi
  if ! command -v codex >/dev/null 2>&1 && ask 'Install Codex CLI using Homebrew?'; then brew install --cask codex; fi
fi
if ! command -v hermes >/dev/null 2>&1; then
  printf '\nHermes missing. Official macOS Desktop installer and CLI instructions:\n'
  printf 'https://hermes-agent.nousresearch.com/docs/getting-started/quickstart\n'
  printf 'Install Hermes Desktop from the official site, open a fresh Terminal and rerun setup.\n'
  if ask 'Open Hermes official installation page in your browser?'; then
    open 'https://hermes-agent.nousresearch.com/docs/getting-started/quickstart' || true
  fi
fi
if command -v gh >/dev/null 2>&1 && ask 'Sign in to your GitHub account with gh auth login?'; then gh auth login; fi
if command -v codex >/dev/null 2>&1 && ask 'Sign in to Codex now?'; then codex login; fi
if command -v hermes >/dev/null 2>&1; then
  if ask 'Configure your Hermes model/provider now?'; then hermes setup --portal; fi
  printf '\nLocal project skills are not trusted automatically. Review .agents/skills/ first.\n'
  if ask 'Trust this repository skill directory for Hermes?'; then hermes skills trust "$ROOT"; fi
fi
printf '\nRunning local verification...\n'
./hq doctor || true
if command -v python3 >/dev/null 2>&1; then
  ./hq test || printf 'Some tests failed. Fix them before trusting this setup.\n'
fi
printf '\nNEXT: ./hq start  (local HQ dashboard)\n'
printf 'THEN: ./hq chat   (after Hermes model setup)\n'
printf 'Full guide: docs/ONBOARDING.md\n\n'
