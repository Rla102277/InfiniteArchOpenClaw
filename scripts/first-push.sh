#!/usr/bin/env bash
# First onboarding commit/push ONLY to the owner's own PRIVATE repository created from the template. Retry safe.
set -euo pipefail
# The template this starter is published from. Your copy must never push here.
TEMPLATE_REPO="${AIHQ_TEMPLATE_REPO:-Rla102277/InfiniteArchOpenClaw}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v gh >/dev/null || { echo 'GitHub CLI missing. Re-run bootstrap.' >&2; exit 3; }
command -v git >/dev/null || { echo 'Git missing. Re-run bootstrap.' >&2; exit 3; }
gh auth status >/dev/null 2>&1 || { echo 'GitHub sign-in required: gh auth login' >&2; exit 3; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo 'Clone your own repository first; not a Git repository.' >&2; exit 3; }
origin="$(git remote get-url origin 2>/dev/null || true)"
[[ -n "$origin" ]] || { echo 'Missing origin. Clone your own repository; do not use Download ZIP.' >&2; exit 3; }
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
 echo "STOP: origin belongs to $owner, but you're signed in as $login. Clone YOUR repository instead." >&2
 exit 4
fi
if [[ "$(printf %s "$repo" | tr "[:upper:]" "[:lower:]")" == "$(printf %s "$TEMPLATE_REPO" | tr "[:upper:]" "[:lower:]")" ]]; then
 echo "STOP: this is the template itself. On GitHub click 'Use this template', choose Private, and clone the new repository." >&2
 exit 4
fi
state="$(gh repo view "$repo" --json visibility,isFork --jq '"\(.visibility) \(.isFork)"' 2>/dev/null || true)"
if [[ "$state" != "PRIVATE false" ]]; then
 case "$state" in
  *" true") echo "STOP: $repo is a fork. Forks of a public template are public. Create your own repository with 'Use this template' -> Private instead." >&2;;
  PUBLIC*|INTERNAL*) echo "STOP: $repo is not private. Your projects and notes would be visible. On GitHub open Settings -> General -> Danger Zone -> Change visibility -> Private, then rerun." >&2;;
  *) echo "STOP: could not confirm $repo is private. Check your connection and GitHub sign-in, then rerun." >&2;;
 esac
 exit 4
fi
branch="$(git symbolic-ref --quiet --short HEAD || true)"
[[ -n "$branch" ]] || { echo 'Detached HEAD; check out your main branch first.' >&2; exit 4; }
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
 echo 'Uncommitted changes detected. Review and commit those separately before the first-push step.' >&2
 git status --short
 exit 4
fi
marker='onboarding/FIRST_BOOTSTRAP.md'
existing_marker=0
if [[ -e "$marker" ]]; then
 existing_marker=1
 git cat-file -e "HEAD:$marker" 2>/dev/null || { echo 'Uncommitted onboarding marker; inspect it first.' >&2; exit 4; }
else
 mkdir -p onboarding
 cat > "$marker" <<'MARKER'
# First local bootstrap

The owner completed the initial local onboarding and approved the first push.
This file intentionally contains no machine identifier, credentials, or private workspace details.
Project discovery and development decisions will be recorded separately.
MARKER
fi
printf '\nReady to push the first onboarding commit to %s (branch %s).\n' "$repo" "$branch"
remote_sha="$(git ls-remote origin "refs/heads/$branch" | awk '{print $1}')" || { echo 'Cannot reach your repository. Retry after checking your connection.' >&2; exit 4; }
if [[ "$existing_marker" == 1 && -n "$remote_sha" && "$remote_sha" == "$(git rev-parse HEAD)" ]]; then
 echo "Already pushed successfully: https://github.com/$repo/tree/$branch"
 exit 0
fi
printf 'Type PUSH to authorize: '
IFS= read -r answer || true
if [[ "$answer" != PUSH ]]; then
 echo 'Canceled. No commit or push performed.'
 if [[ "$existing_marker" == 0 ]]; then
  rm -f "$marker"
  rmdir onboarding 2>/dev/null || true
 fi
 exit 1
fi
if [[ "$existing_marker" == 0 ]]; then
 if ! git config user.name >/dev/null 2>&1; then git config user.name "$login"; fi
 if ! git config user.email >/dev/null 2>&1; then
  id="$(gh api user --jq '.id')"
  git config user.email "${id}+${login}@users.noreply.github.com"
 fi
 git add -- "$marker"
 git commit -m 'Initialize my Personal AI HQ workspace'
fi
if ! git push origin "HEAD:refs/heads/$branch"; then
 echo 'Push failed; commit is saved locally. Rerun ./scripts/first-push.sh to retry without a duplicate commit.' >&2
 exit 5
fi
printf '\nFirst commit pushed successfully: https://github.com/%s/tree/%s\n' "$repo" "$branch"
