#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
AUTO=0
LAUNCH=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --yes) AUTO=1;;
    --launch) LAUNCH=1;;
    --check) CHECK=1;;
    *) echo 'Usage: ./bootstrap.sh [--yes] [--launch] [--check]' >&2; exit 2;;
  esac
done
if [[ "$CHECK" == 1 ]]; then exec ./setup.sh --check; fi
if [[ "$(uname -s)" != Darwin ]]; then echo 'This bootstrap supports macOS only.' >&2; exit 2; fi
printf '\nPERSONAL AI HQ — ONE-TIME MAC BOOTSTRAP\n\n'
confirm() {
  local answer=''
  if [[ "$AUTO" == 1 ]]; then return 1; fi
  printf '%s [y/N]: ' "$1"
  IFS= read -r answer || true
  [[ "$answer" =~ ^([yY]|yes|YES)$ ]]
}
if [[ "$AUTO" == 1 ]]; then
  echo '--yes uses installed tools only; installation and login offers are skipped. PUSH still requires approval.'
fi
if ! command -v git >/dev/null 2>&1 && command -v xcode-select >/dev/null 2>&1; then
  if confirm 'Open Apple Command Line Tools installer for Git?'; then
    xcode-select --install
    echo 'Finish the Apple installation, then rerun ./bootstrap.sh.'
    exit 3
  fi
fi
for tool in git python3 gh; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    package="$tool"
    if [[ "$tool" == python3 ]]; then package=python; fi
    if command -v brew >/dev/null 2>&1; then
      if confirm "Install $tool using Homebrew (brew install $package)?"; then brew install "$package"; fi
    else
      echo "$tool is missing. Homebrew instructions: https://brew.sh"
    fi
  fi
done
for tool in git python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required; install it and rerun ./bootstrap.sh." >&2; exit 3; }
done
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo 'Python 3.10+ is required; update Python and rerun ./bootstrap.sh.' >&2; exit 3;
}
python3 scripts/seed_local.py
./hq init
./hq validate
./hq doctor
./hq test
printf '\nLOCAL BOOTSTRAP COMPLETE\n'
printf 'Start dashboard: ./hq start\nWorkspace: %s\n' "${AIHQ_HOME:-$HOME/PersonalAIHQ}"
printf 'Hermes and Codex are optional. For Hermes installation and a local model, see docs/ONBOARDING.md.\n'
if command -v gh >/dev/null 2>&1; then
  if ! gh auth status >/dev/null 2>&1; then
    if confirm 'Connect to GitHub now (gh auth login)?'; then gh auth login; fi
  fi
  if gh auth status >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
    echo 'Next: validate your OWN GitHub fork and approve the first onboarding push.'
    ./scripts/first-push.sh
  else
    echo 'First push postponed: sign into GitHub and clone your own fork, then run ./scripts/first-push.sh.'
  fi
else
  echo 'First push postponed: install GitHub CLI, run gh auth login, then run ./scripts/first-push.sh in your fork.'
fi
if [[ "$LAUNCH" == 1 ]]; then exec ./hq start; fi
