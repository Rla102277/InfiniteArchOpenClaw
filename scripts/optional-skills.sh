#!/usr/bin/env bash
set -euo pipefail
cat <<'TXT'
OPTIONAL THIRD-PARTY SKILLS — REVIEW FIRST

The starter includes its OWN small, portable skills. Do not blindly install all
third-party prompt packs or grant them machine-wide permissions.

Pocock: https://github.com/mattpocock/skills
Addy Osmani: https://github.com/addyosmani/agent-skills
pstack portable: https://github.com/backnotprop/pstack

To inspect a skill without installing it:
  hermes skills search <keyword>
  hermes skills inspect <source/path>

Choose the exact skills you need, audit their shell commands and permissions,
record pinned repository commit SHAs in docs/THIRD_PARTY_SKILLS.md, and only
then install through Hermes' scanner. For Codex, follow the official plugin
instructions in each source repository. Compatibility varies by agent harness.

Do not run `npx skills add ...` unreviewed as part of this bootstrap.
TXT
