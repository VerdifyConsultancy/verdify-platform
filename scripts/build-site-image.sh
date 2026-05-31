#!/usr/bin/env bash
# build-site-image.sh — Build the verdify-site (lab.verdify.ai) k3s image.
#
# WHY THIS WRAPPER: the authoritative Quartz CONTENT is the symlink
#   verdify-site/content -> /mnt/iris/verdify-vault/website
# Docker COPY does NOT follow symlinks and the vault lives outside the build
# context, so we materialize the vault into a REAL `content/` dir (rsync -L,
# dereference), run `docker build` with verdify-site/ as the context, then clean
# up the materialized dir. The image bakes the content in at a known git sha.
#
# Output: ghcr.io/verdifyconsultancy/verdify-site:sha-<gitsha>. Prints the local
# image digest (RepoDigests after push, or the image ID + Config digest locally)
# so the kustomize overlay images: transformer can pin @sha256.
#
# Usage:
#   scripts/build-site-image.sh                 # build only (local), prints tag+id
#   PUSH=1 scripts/build-site-image.sh          # build + push, prints repo digest
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE_DIR="$REPO_ROOT/verdify-site"
VAULT="${VERDIFY_SITE_CONTENT:-/mnt/iris/verdify-vault/website}"
IMAGE="${IMAGE:-ghcr.io/verdifyconsultancy/verdify-site}"

# Git sha of the verdify-site nested repo (its own content version), falling back
# to the monorepo sha if the nested repo is unavailable.
if git -C "$SITE_DIR" rev-parse --short=12 HEAD >/dev/null 2>&1; then
  GITSHA="$(git -C "$SITE_DIR" rev-parse --short=12 HEAD)"
else
  GITSHA="$(git -C "$REPO_ROOT" rev-parse --short=12 HEAD)"
fi
TAG="${TAG:-sha-$GITSHA}"
REF="$IMAGE:$TAG"

if [ ! -d "$VAULT" ]; then
  echo "ERROR: content source not found: $VAULT" >&2
  exit 1
fi

CONTENT_DIR="$SITE_DIR/content"
MATERIALIZED=0
cleanup() {
  if [ "$MATERIALIZED" = "1" ] && [ -d "$CONTENT_DIR" ] && [ ! -L "$CONTENT_DIR" ]; then
    rm -rf "$CONTENT_DIR"
    # Restore the dev symlink so the live rebuild-site.sh flow is unaffected.
    ln -s "$VAULT" "$CONTENT_DIR"
  fi
}
trap cleanup EXIT

# Materialize the vault into a real dir (replace the symlink for the build).
if [ -L "$CONTENT_DIR" ]; then
  rm -f "$CONTENT_DIR"
fi
MATERIALIZED=1
mkdir -p "$CONTENT_DIR"
# -L dereferences symlinks; exclude vault noise that should never ship.
rsync -aL --delete \
  --exclude '.obsidian/' \
  --exclude '.git/' \
  --exclude '@eaDir/' \
  --exclude '.DS_Store' \
  --exclude '.quartz-cache/' \
  --exclude 'private/' \
  --exclude 'templates/' \
  "$VAULT/" "$CONTENT_DIR/"

echo ">> building $REF (content sha $GITSHA, $(find "$CONTENT_DIR" -type f | wc -l) content files)"
docker build \
  -f "$SITE_DIR/Dockerfile.k3s" \
  -t "$REF" \
  "$SITE_DIR"

if [ "${PUSH:-0}" = "1" ]; then
  docker push "$REF"
  echo ">> pushed; repo digest:"
  docker inspect --format '{{ index .RepoDigests 0 }}' "$REF"
else
  echo ">> built (local, not pushed). image id + config digest:"
  docker inspect --format 'id={{ .Id }}' "$REF"
fi
echo ">> tag: $REF"
