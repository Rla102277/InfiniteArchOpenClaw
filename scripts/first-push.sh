#!/usr/bin/env bash
# Create an auditable first commit and push ONLY to the authenticated user's fork.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v gh >/dev/null || { echo 'GitHub CLI missing. Re-run bootstrap.' >&2; exit 3; }
command -v git >/dev/null || { echo 'Git missing. Re-run bootstrap.' >&2; exit 3; }
gh auth status >/dev/null 2>&1 || { echo 'GitHub sign-in required: gh auth login' >&2; exit 3; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo 'Clone your fork first; not a Git repository.' >&2; exit 3; }
origin="$(git remote get-url origin 2>/dev/null || true)"
[[ -n "$origin" ]] || { echo 'Missing origin. Clone your fork; do not use Download ZIP.' >&2; exit 3; }
# GitHub HTTPS or SSH remotes only, never arbitrary remote hosts.
case "$origin" in
 https://github.com/*|git@github.com:*|ssh://git@github.com/*) ;;
 *) echo "Refusing push to unknown remote: $origin" >&2; exit 3;;
esac
repo="$(printf '%s' "$origin" | sed -E 's#^(https://github.com/|git@github.com:|ssh://git@github.com/)##; s#\.git$##; s#/$##')"
[[ "$repo" =~ ^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$ ]] || { echo 'Unexpected GitHub remote path.' >&2; exit 3; }
owner="${repo%%/*}"
login="$(gh api user --jq '.login')"
if [[ "$(printf %s "$owner" | tr "[:upper:]" "[:lower:]")" != "$(printf %s "$login" | tr "[:upper:]" "[:lower:]")" ]]; then
 echo "STOP: origin belongs to $owner, but you're signed in as $login. Clone YOUR fork instead." >&2
 exit 4
fi
fork="$(gh repo view "$repo" --json isFork --jq '.isFork' 2>/dev/null || true)"
if [[ "$fork" != true ]]; then
 echo "STOP: $repo is not a GitHub fork. This flow requires Fork -> Clone -> Bootstrap." >&2
 exit 4
fi
branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ -n "$branch" ]] || { echo 'Detached HEAD; checkout your fork branch first.' >&2; exit 4; }
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
 echo 'Uncommitted changes detected. Review and commit those separately before the first-push step.' >&2
 git status --short
 exit 4
fi
if [[ -f onboarding/FIRST_BOOTSTRAP.md ]]; then
 echo 'First bootstrap commit already exists; skipping.'
 exit 0
fi
mkdir -p onboarding
cat > onboarding/FIRST_BOOTSTRAP.md <<'MARKER'
# First local bootstrap

The owner completed the initial local onboarding and approved the first push.
This file intentionally contains no machine identifier, credentials, or private workspace details.
Project discovery and development decisions will be recorded separately.
MARKER
printf '\nReady to push the first onboarding commit to %s (branch %s).\n' "$repo" "$branch"
printf 'Type PUSH to authorize: '
IFS= read -r answer || true
if [[ "$answer" != PUSH ]]; then
 echo 'Canceled. No commit or push performed.'
 rm -f onboarding/FIRST_BOOTSTRAP.md
 rmdir onboarding 2>/dev/null || true
 exit 1
fi
if ! git config user.name >/dev/null 2>&1; then git config user.name "$login"; fi
if ! git config user.email >/dev/null 2>&1; then
 id="$(gh api user --jq '.id')"
 git config user.email "${id}+${login}@users.noreply.github.com"
fi
git add -- onboarding/FIRST_BOOTSTRAP.md
git commit -m 'Initialize my Personal AI HQ workspace'
git push origin "HEAD:refs/heads/$branch"
printf '\nFirst commit pushed successfully: https://github.com/%s/tree/%s\n' "$repo" "$branch"
