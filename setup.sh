#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
if [[ "${1:-}" == --check && "$#" == 1 ]]; then
  printf '\nPERSONAL AI HQ — TOOL CHECK (no changes)\n'
  for tool in git python3 brew gh codex hermes; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf '[OK]      %s\n' "$tool"
    else
      printf '[MISSING] %s\n' "$tool"
    fi
  done
  echo 'Git and Python 3.10+ support local bootstrap. GitHub CLI supports fork push; Hermes/Codex are optional.'
  exit 0
fi
exec ./bootstrap.sh "$@"
