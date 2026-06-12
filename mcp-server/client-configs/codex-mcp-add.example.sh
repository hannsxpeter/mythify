#!/usr/bin/env sh
set -eu

codex mcp add mythify \
  --env MYTHIFY_DIR=/absolute/path/to/your/project/.mythify \
  --env MYTHIFY_TRIAGE_ENGINE=codex-cli \
  --env MYTHIFY_FANOUT_ENGINE=codex-cli \
  -- node /absolute/path/to/mythify/mcp-server/src/index.js
