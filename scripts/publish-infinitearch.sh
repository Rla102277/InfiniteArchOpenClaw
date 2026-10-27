#!/usr/bin/env bash
# Owner-only publish of starter source plus distributable ZIP into InfiniteArchOpenClaw.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v gh >/dev/null || { echo 'Install GitHub CLI (gh) first.' >&2; exit 3; }
command -v git >/dev/null || { echo 'Install Git first.' >&2; exit 3; }
command -v zip >/dev/null || { echo 'zip command required.' >&2; exit 3; }
gh auth status >/dev/null 2>&1 || { echo 'Run gh auth login first.' >&2; exit 3; }
[[ "$(gh api user --jq .login | tr '[:upper:]' '[:lower:]')" == 'rla102277' ]] || { echo 'Only Rla102277 may publish to the upstream repository.' >&2; exit 4; }
remote='Rla102277/InfiniteArchOpenClaw'
remote_url="https://github.com/$remote.git"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/repo"
gh repo clone "$remote" "$work/repo"
# Never delete upstream files. Stop if there's existing tracked source rather than overwriting it.
if [[ -e "$work/repo/bootstrap.sh" || -e "$work/repo/aihq" ]]; then
 echo 'Upstream already has starter content. Review/update it through a PR; refusing overwrite.' >&2
 exit 4
fi
# Create a reproducible snapshot, with no accidental .git files or local runtime state.
mkdir -p "$work/repo/releases"
(cd "$ROOT/.." && zip -qr "$work/repo/releases/Personal_AI_HQ_Bootstrap_v0.3.zip" "$(basename "$ROOT")" -x '*/__pycache__/*' '*/.git/*' '*/.DS_Store' '*/.env' '*/.env.*' '*/.local/*')
# Copy starter sources, including hidden .agents, .github and .gitignore.
(cd "$ROOT" && tar -cf - --exclude=.git --exclude=__pycache__ --exclude=.DS_Store --exclude=.local --exclude=.env --exclude='.env.*' .) | (cd "$work/repo" && tar -xf -)
cd "$work/repo"
if ! git rev-parse --verify HEAD >/dev/null 2>&1; then git checkout -B main; fi
printf '\nThis will commit the full starter and ZIP to %s.\n' "$remote"
git status --short
printf 'Type PUBLISH to authorize: '
IFS= read -r ans || true
[[ "$ans" == PUBLISH ]] || { echo 'Canceled; no changes published.'; exit 1; }
git add -A -- . ':!*.env' ':!.env.*'
git diff --cached --quiet && { echo 'Nothing to publish.'; exit 0; }
git commit -m 'Publish Personal AI HQ fork-first bootstrap v0.3'
git push -u origin HEAD:main
printf '\nPublished: https://github.com/%s\n' "$remote"
