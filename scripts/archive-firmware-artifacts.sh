#!/usr/bin/env bash
# Archive the exact ESPHome build outputs for a firmware version.
#
# Run after ESPHome compile. With --promote-last-good, also updates the
# rollback target used by scripts/firmware-rollback.sh.

set -euo pipefail

usage() {
    echo "Usage: $0 FIRMWARE_VERSION [--promote-last-good]" >&2
}

FW_VERSION="${1:-}"
if [[ -z "$FW_VERSION" ]]; then
    usage
    exit 2
fi
shift || true

PROMOTE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --promote-last-good) PROMOTE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
    esac
done

REPO_ROOT="${REPO_ROOT:-$(git rev-parse --show-toplevel)}"
REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
cd "$REPO_ROOT"
BUILD_DIR="$REPO_ROOT/firmware/.esphome/build/greenhouse/.pioenvs/greenhouse"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$REPO_ROOT/firmware/artifacts}"
DEST="$ARTIFACT_ROOT/$FW_VERSION"
PROVENANCE_DIR="$DEST/provenance"
SOURCE_SNAPSHOT_DIR="$DEST/source-snapshot"
GENERATED_SOURCE_DIR="$DEST/generated-src"

required=(
    firmware.ota.bin
    firmware.bin
    firmware.elf
    firmware.map
)

for f in "${required[@]}"; do
    if [[ ! -f "$BUILD_DIR/$f" ]]; then
        echo "Missing build artifact: $BUILD_DIR/$f" >&2
        exit 1
    fi
done

mkdir -p "$DEST"
for f in "${required[@]}"; do
    cp "$BUILD_DIR/$f" "$DEST/$f"
done

if [[ -f "$BUILD_DIR/project_description.json" ]]; then
    cp "$BUILD_DIR/project_description.json" "$DEST/project_description.json"
fi
if [[ -f "$REPO_ROOT/firmware/.esphome/storage/greenhouse.yaml.json" ]]; then
    cp "$REPO_ROOT/firmware/.esphome/storage/greenhouse.yaml.json" "$DEST/esphome-storage.json"
fi

{
    echo "firmware_version=$FW_VERSION"
    echo "archived_at=$(date -Is)"
    echo "source_ref=$(git rev-parse --abbrev-ref HEAD)"
    echo "source_sha=$(git rev-parse HEAD)"
    if git diff --quiet -- . && git diff --cached --quiet -- .; then
        echo "source_dirty=0"
    else
        echo "source_dirty=1"
    fi
    if [[ -n "${FIRMWARE_DEPLOYED_AT:-}" ]]; then
        echo "deployed_at=$FIRMWARE_DEPLOYED_AT"
    fi
    echo "esphome_bin=${ESPHOME_BIN:-${VENV:-.venv}/bin/esphome}"
    echo "build_dir=$BUILD_DIR"
    echo "artifact_dir=$DEST"
    echo "source_provenance_dir=$PROVENANCE_DIR"
    echo "source_snapshot_dir=$SOURCE_SNAPSHOT_DIR"
    echo "generated_source_dir=$GENERATED_SOURCE_DIR"
    echo "addr2line=/home/jason/.platformio/packages/toolchain-xtensa-esp-elf/bin/xtensa-esp32-elf-addr2line -pfiaC -e firmware.elf <PC> <BT...>"
} > "$DEST/metadata.env"

mkdir -p "$PROVENANCE_DIR" "$SOURCE_SNAPSHOT_DIR" "$GENERATED_SOURCE_DIR"
git status --short --branch --untracked-files=all > "$PROVENANCE_DIR/git-status.txt"
git status --porcelain=v1 --branch --untracked-files=all -z > "$PROVENANCE_DIR/git-status.porcelain.z"
git diff --stat -- . > "$PROVENANCE_DIR/git-diff.stat"
git diff --name-status -- . > "$PROVENANCE_DIR/git-diff.name-status"
git diff --binary -- . > "$PROVENANCE_DIR/git-diff.patch"
git diff --cached --binary -- . > "$PROVENANCE_DIR/git-diff-cached.patch"
git ls-files --others --exclude-standard -z -- \
    firmware/greenhouse.yaml \
    firmware/greenhouse \
    firmware/lib \
    > "$PROVENANCE_DIR/untracked-source-files.z"
tr '\0' '\n' < "$PROVENANCE_DIR/untracked-source-files.z" > "$PROVENANCE_DIR/untracked-source-files.txt"

for path in firmware/greenhouse.yaml firmware/greenhouse firmware/lib; do
    if [[ -e "$path" ]]; then
        mkdir -p "$SOURCE_SNAPSHOT_DIR/$(dirname "$path")"
        cp -a "$path" "$SOURCE_SNAPSHOT_DIR/$path"
    fi
done

while IFS= read -r -d '' path; do
    [[ -f "$path" ]] || continue
    mkdir -p "$PROVENANCE_DIR/untracked/$(dirname "$path")"
    cp -a "$path" "$PROVENANCE_DIR/untracked/$path"
done < "$PROVENANCE_DIR/untracked-source-files.z"

for path in \
    firmware/.esphome/build/greenhouse/src/main.cpp \
    firmware/.esphome/build/greenhouse/src/esphome.h
do
    if [[ -f "$path" ]]; then
        mkdir -p "$GENERATED_SOURCE_DIR/$(dirname "$path")"
        cp -a "$path" "$GENERATED_SOURCE_DIR/$path"
    fi
done

(
    cd "$DEST"
    sha256sum "${required[@]}" > SHA256SUMS
    find provenance source-snapshot generated-src -type f -print0 \
        | sort -z \
        | xargs -0r sha256sum > SOURCE_SHA256SUMS
)

if [[ "$PROMOTE" -eq 1 ]]; then
    cp "$DEST/firmware.ota.bin" "$ARTIFACT_ROOT/last-good.ota.bin"
    printf '%s\n' "$FW_VERSION" > "$ARTIFACT_ROOT/last-good.version"
    cp "$DEST/metadata.env" "$ARTIFACT_ROOT/last-good.metadata.env"
    if [[ -n "${FIRMWARE_DEPLOYED_AT:-}" ]]; then
        touch -d "$FIRMWARE_DEPLOYED_AT" "$ARTIFACT_ROOT/last-good.ota.bin"
    fi
fi

echo "Archived firmware artifacts: $DEST"
if [[ "$PROMOTE" -eq 1 ]]; then
    echo "Promoted rollback target: $FW_VERSION"
fi
