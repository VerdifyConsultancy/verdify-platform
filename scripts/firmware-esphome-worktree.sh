#!/usr/bin/env bash
# Run ESPHome against the current git worktree's firmware/greenhouse.yaml.
#
# The old deploy path compiled /srv/greenhouse/esphome, a symlink farm into
# /srv/verdify. That made OTA source depend on whichever tree happened to be
# mounted there. This wrapper keeps the source tree explicit: the current repo
# worktree is always the firmware source, and /srv/greenhouse/esphome is used
# only as the runtime secrets location.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

if [[ -z "${ESPHOME_BIN:-}" ]]; then
    if [[ -x "$REPO_ROOT/.venv/bin/esphome" ]]; then
        ESPHOME_BIN="$REPO_ROOT/.venv/bin/esphome"
    elif [[ -x /srv/greenhouse/.venv/bin/esphome ]]; then
        ESPHOME_BIN="/srv/greenhouse/.venv/bin/esphome"
    else
        ESPHOME_BIN="$(command -v esphome || true)"
    fi
fi
SECRETS_SRC="${SECRETS_SRC:-/srv/greenhouse/esphome/secrets.yaml}"
CONFIG="firmware/greenhouse.yaml"
SECRET_LINK="firmware/secrets.yaml"

if [[ ! -x "$ESPHOME_BIN" ]]; then
    echo "ESPHome binary not found or not executable: ${ESPHOME_BIN:-<unset>}" >&2
    echo "Install tooling with: make setup BOOTSTRAP_EXTRAS=dev" >&2
    exit 2
fi
if [[ ! -f "$SECRETS_SRC" ]]; then
    echo "ESPHome secrets not found: $SECRETS_SRC" >&2
    exit 2
fi
if [[ -e "$SECRET_LINK" ]]; then
    echo "Refusing to overwrite $SECRET_LINK" >&2
    exit 2
fi

ln -s "$SECRETS_SRC" "$SECRET_LINK"
cleanup() {
    rm -f "$SECRET_LINK"
}
trap cleanup EXIT

"$ESPHOME_BIN" "$@" "$CONFIG"
