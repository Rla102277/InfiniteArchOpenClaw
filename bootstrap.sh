#!/usr/bin/env bash
# Bootstrap a local Personal AI HQ using approved system installers.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
AUTO=0; LAUNCH=0
for arg in "$@"; do case "$arg" in --yes) AUTO=1;; --launch) LAUNCH=1;; --check) exec ./setup.sh --check;; *) echo "Usage: ./bootstrap.sh [--yes] [--launch] [--check]" >&2; exit 2;; esac; done
if [[ "$(uname -s)" != Darwin ]]; then echo 'This bootstrap supports macOS only.' >&2; exit 2; fi
printf '\nPERSONAL AI HQ — ONE-TIME MAC BOOTSTRAP\n\n'
confirm() { local r; if [[ "$AUTO" == 1 ]]; then return 0; fi; printf '%s [y/N]: ' "$1"; read -r r || true; [[ "$r" =~ ^([yY]|yes|YES)$ ]]; }
if ! command -v xcode-select >/dev/null 2>&1; then echo 'Apple developer tools unavailable.'; exit 2; fi
if ! xcode-select -p >/dev/null 2>&1; then
  echo 'Apple Command Line Tools are required. The Mac will show an installation dialog.'
  if confirm 'Open Apple developer tools installer?'; then xcode-select --install || true; fi
  echo 'Finish that installation, then rerun ./bootstrap.sh.'; exit 3
fi
if ! command -v brew >/dev/null 2>&1; then
  echo 'Homebrew is required to install Git, Python, GitHub CLI, and Codex.'
  echo 'Official installer: https://brew.sh'
  if confirm 'Open Homebrew setup instructions?'; then open https://brew.sh; fi
  echo 'Install Homebrew from its official website, then rerun ./bootstrap.sh.'; exit 3
fi
for pkg in git python gh; do
 if ! command -v "$pkg" >/dev/null 2>&1; then
   if confirm "Install $pkg using Homebrew?"; then brew install "$pkg"; else echo "Skipping $pkg"; fi
 fi
done
if ! command -v codex >/dev/null 2>&1; then
 if confirm 'Install OpenAI Codex CLI using the official Homebrew cask?'; then brew install --cask codex; fi
fi
if ! command -v hermes >/dev/null 2>&1; then
  echo 'Hermes Desktop is best installed from its official signed macOS installer:'
  echo 'https://hermes-agent.nousresearch.com/docs/getting-started/installation'
  if confirm 'Open official Hermes installation page?'; then open https://hermes-agent.nousresearch.com/docs/getting-started/installation; fi
  echo 'After installing Hermes Desktop, return and rerun ./bootstrap.sh.'
fi
if ! command -v python3 >/dev/null 2>&1; then echo 'Python is not installed; cannot seed workspace.' >&2; exit 3; fi
python3 scripts/seed_local.py
if command -v gh >/dev/null 2>&1; then
 if ! gh auth status >/dev/null 2>&1; then
  if confirm 'Connect to GitHub now (browser login)?'; then gh auth login; fi
 fi
fi
if command -v codex >/dev/null 2>&1; then
 if confirm 'Run Codex login now (if already logged in you can skip)?'; then codex login; fi
fi
if command -v hermes >/dev/null 2>&1; then
 if confirm 'Configure Hermes model/provider now?'; then hermes model; fi
 if confirm 'Review and trust bundled LOCAL skills in this repository with Hermes?'; then hermes skills trust "$ROOT"; fi
fi
if [[ ! -d .git ]]; then
 if command -v git >/dev/null 2>&1; then git init -b main; fi
fi
./hq doctor
./hq test
printf '\nBOOTSTRAP COMPLETE\n'
printf 'Start dashboard: ./hq start\nStart assistant: ./hq chat\n'
printf 'First commit: ./scripts/first-push.sh (retry safe)\n'
printf 'Workspace: %s\n' "${AIHQ_HOME:-$HOME/PersonalAIHQ}"
if git remote get-url origin >/dev/null 2>&1 && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1 && command -v hermes >/dev/null 2>&1 && command -v codex >/dev/null 2>&1; then
  echo 'Next: create the first commit in your OWN private GitHub repository.'
  ./scripts/first-push.sh
else
  echo 'First push postponed: finish Hermes/Codex installs, sign into GitHub, and clone your own private repository made with Use this template. Rerun ./bootstrap.sh.'
fi
if [[ "$LAUNCH" == 1 ]]; then exec ./hq start; fi
