#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${HERMES_IRIS_RUNTIME_DIR:-/var/lib/verdify/hermes/iris}"
ENV_FILE="${HERMES_IRIS_ENV_FILE:-/etc/verdify/hermes-iris.env}"
RUNTIME_UID="${HERMES_IRIS_RUNTIME_UID:-10000}"
RUNTIME_GID="${HERMES_IRIS_RUNTIME_GID:-10000}"

if [[ ! -f "$ENV_FILE" ]] && sudo test -f "$RUNTIME_DIR/.env"; then
  sudo install -m 640 -o root -g "$(id -gn)" "$RUNTIME_DIR/.env" "$ENV_FILE"
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing Hermes env file: $ENV_FILE" >&2
  echo "Create it with OPENAI_API_KEY, VERDIFY_MCP_TOKEN, and HERMES_IRIS_API_KEY." >&2
  exit 1
fi

sudo install -d -m 700 -o "$RUNTIME_UID" -g "$RUNTIME_GID" "$RUNTIME_DIR"
sudo install -m 600 -o "$RUNTIME_UID" -g "$RUNTIME_GID" \
  "$ROOT/hermes/iris/config.yaml" "$RUNTIME_DIR/config.yaml"
sudo install -m 600 -o "$RUNTIME_UID" -g "$RUNTIME_GID" \
  "$ROOT/hermes/iris/SOUL.md" "$RUNTIME_DIR/SOUL.md"
sudo install -m 600 -o "$RUNTIME_UID" -g "$RUNTIME_GID" \
  "$ROOT/hermes/iris/README.md" "$RUNTIME_DIR/README.md"

echo "Hermes config synced to $RUNTIME_DIR"
echo "Hermes env file present at $ENV_FILE"
