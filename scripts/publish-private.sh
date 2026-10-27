#!/usr/bin/env bash
# Explicitly publish a fresh local copy to a new private GitHub repository.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
NAME="${1:-}"
if [[ -z "$NAME" || ! "$NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  printf 'Usage: ./scripts/publish-private.sh your-starter-repo-name\n' >&2; exit 2
fi
command -v gh >/dev/null || { echo 'Missing gh. Run ./setup.sh'; exit 2; }
command -v git >/dev/null || { echo 'Missing git. Run ./setup.sh'; exit 2; }
if ! gh auth status >/dev/null 2>&1; then echo 'Sign into GitHub with gh auth login first.'; exit 2; fi
if [[ -d .git ]] && git remote get-url origin >/dev/null 2>&1; then
  echo 'This folder already has an origin remote. No changes made.'; exit 2
fi
printf '\nThis will create a new PRIVATE GitHub repository named %s and push these files.\n' "$NAME"
printf 'IMPORTANT: all files under projects/ may contain private ideas or business information.\n'
printf 'You should review the file list before publishing.\n\n'
if [[ ! -d .git ]]; then git init -b main; fi
git add -n .
printf '\nTo confirm publication, type PUBLISH: '
read -r ANSWER
if [[ "$ANSWER" != PUBLISH ]]; then echo 'Canceled.'; exit 1; fi
git add .
if ! git diff --cached --quiet; then
  git commit -m "Initialize Personal AI HQ starter"
fi
# --private makes the safe default explicit. Publishing is a separate human action.
gh repo create "$NAME" --private --source=. --remote=origin --push
echo 'Private GitHub repository created. In GitHub Settings > General, enable Template repository to let friends use it.'
