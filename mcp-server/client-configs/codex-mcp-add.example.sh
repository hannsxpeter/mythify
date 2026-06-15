#!/usr/bin/env sh
set -eu

codex mcp add mythify \
  --env MYTHIFY_DIR=/absolute/path/to/your/project/.mythify \
  --env MYTHIFY_HOST_PLATFORM=codex-desktop \
  -- node /absolute/path/to/mythify/mcp-server/src/index.js
