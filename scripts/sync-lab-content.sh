#!/usr/bin/env bash
# sync-lab-content.sh — Snapshot the curated public vault subtree into the
# verdify-lab content repo and open a PR (#124 / #219, lane 219).
#
# WHY THIS IS FLEET-SIDE (not GitHub-hosted CI): the production lab.verdify.ai
# content is the curated public subset of the Obsidian vault (the vault `website/`
# subtree), which lives ONLY on the synced fleet replica
# (~/Iris/verdify-vault/website on an operator Mac, /mnt/iris/verdify-vault/website
# on the fleet). GitHub-hosted runners cannot reach it. This script therefore runs
# WHERE THE VAULT IS (an operator Mac via the laptop-root control plane, or a
# fleet host / self-hosted runner with the vault mounted) and PUSHES a content
# snapshot into the build repo. CI then builds the image deterministically from
# the committed snapshot (no live coupling). This is the durable split:
#   fleet (vault access)  ->  snapshot PR  ->  GitHub CI (reproducible build).
#
# WHAT IT DOES
#   1. rsync the curated `website/` subtree out of the vault, SCRUBBING private/
#      templates/Obsidian/Syncthing/metadata paths (privacy curation).
#   2. Stage it as content-snapshot/ in a checkout of the verdify-lab content repo
#      (VerdifyConsultancy/verdify-site-legacy, branch v4).
#   3. If it changed, commit on a branch and open a PR (gh).
#
# It never edits the vault. It only reads the public website/ subtree.
#
# USAGE
#   scripts/sync-lab-content.sh [--dry-run] [--no-pr] [--include-video]
#
# ENV (overridable)
#   VAULT_WEBSITE   curated vault subtree (default: first existing of the two below)
#   LAB_REPO        owner/name of the content/build repo (default verdify-site-legacy)
#   LAB_BRANCH      base branch of the content repo (default v4)
#   WORKDIR         scratch checkout dir (default: mktemp)
set -euo pipefail

DRY_RUN=false
OPEN_PR=true
INCLUDE_VIDEO=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --no-pr) OPEN_PR=false; shift ;;
    --include-video) INCLUDE_VIDEO=true; shift ;;
    -h|--help) sed -n '1,40p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Resolve the curated vault subtree (operator Mac OR fleet host).
default_vault=""
for cand in \
    "${VAULT_WEBSITE:-}" \
    "$HOME/Iris/verdify-vault/website" \
    "/mnt/iris/verdify-vault/website" \
    "/Users/jason/Iris/verdify-vault/website"; do
  if [[ -n "$cand" && -d "$cand" ]]; then default_vault="$cand"; break; fi
done
VAULT_WEBSITE="${default_vault}"
if [[ -z "$VAULT_WEBSITE" || ! -d "$VAULT_WEBSITE" ]]; then
  echo "ERROR: curated vault subtree not found (set VAULT_WEBSITE=.../verdify-vault/website)" >&2
  echo "       this script MUST run where the synced vault replica is reachable." >&2
  exit 1
fi

LAB_REPO="${LAB_REPO:-VerdifyConsultancy/verdify-site-legacy}"
LAB_BRANCH="${LAB_BRANCH:-v4}"
WORKDIR="${WORKDIR:-$(mktemp -d -t sync-lab-content.XXXXXX)}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BRANCH="lab-content/sync-${TS}"

echo "[sync-lab-content] vault subtree : $VAULT_WEBSITE"
echo "[sync-lab-content] content repo  : $LAB_REPO ($LAB_BRANCH)"
echo "[sync-lab-content] workdir       : $WORKDIR"
echo "[sync-lab-content] include video : $INCLUDE_VIDEO"

# Privacy curation: the website/ subtree is already the public subset, but we
# defensively scrub anything non-public/operational that could ride along.
RSYNC_EXCLUDES=(
  --exclude '.obsidian/'
  --exclude '@eaDir/'
  --exclude '.DS_Store'
  --exclude '.stfolder/'
  --exclude '.stignore'
  --exclude '.stversions/'
  --exclude '.sync-conflict-*'
  --exclude 'private/'
  --exclude 'templates/'
  --exclude '.trash/'
)
# The ~350MB launch video is LFS-managed via the repo .gitattributes; exclude it
# from the default text+image snapshot unless --include-video is passed.
if [[ "$INCLUDE_VIDEO" != true ]]; then
  RSYNC_EXCLUDES+=( --exclude 'static/video/' )
fi

# Checkout the content repo.
git clone --quiet --branch "$LAB_BRANCH" "https://github.com/${LAB_REPO}.git" "$WORKDIR/repo"
cd "$WORKDIR/repo"

# Replace content-snapshot/ wholesale (rsync --delete) so removed vault pages are
# dropped from the snapshot too. Preserve repo-managed helper files.
mkdir -p content-snapshot
# Keep the generated-marker README and the video placeholder; restore after sync.
[[ -f content-snapshot/README.snapshot.md ]] && cp content-snapshot/README.snapshot.md /tmp/.snap-readme.$$ || true
[[ -f content-snapshot/static/video/README.md ]] && { mkdir -p /tmp/.snap-vid.$$; cp content-snapshot/static/video/README.md /tmp/.snap-vid.$$/; } || true

rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$VAULT_WEBSITE"/ content-snapshot/

# Restore generated markers.
[[ -f /tmp/.snap-readme.$$ ]] && mv /tmp/.snap-readme.$$ content-snapshot/README.snapshot.md || true
if [[ "$INCLUDE_VIDEO" != true && -d /tmp/.snap-vid.$$ ]]; then
  mkdir -p content-snapshot/static/video
  mv /tmp/.snap-vid.$$/README.md content-snapshot/static/video/README.md
  rm -rf /tmp/.snap-vid.$$
fi

md_count="$(find content-snapshot -name '*.md' -type f | wc -l | tr -d ' ')"
file_count="$(find content-snapshot -type f | wc -l | tr -d ' ')"
echo "[sync-lab-content] snapshot: ${md_count} markdown, ${file_count} files"

# Stage so the change check also catches NEW (untracked) snapshot files —
# `git diff` alone ignores untracked paths (e.g. the very first snapshot on a
# branch that has none yet).
git add -A content-snapshot
if git diff --cached --quiet -- content-snapshot; then
  echo "[sync-lab-content] no content change — nothing to do."
  exit 0
fi

if [[ "$DRY_RUN" == true ]]; then
  echo "[sync-lab-content] --dry-run: changes detected, not committing. Diff stat:"
  git diff --cached --stat -- content-snapshot | tail -20
  exit 0
fi

# Move the staged snapshot onto a fresh branch and commit.
git checkout -b "$BRANCH"
git -c user.name="laptop-root" -c user.email="jason@vallery.net" commit -q -m \
  "chore(lab): refresh content-snapshot from vault website/ (${TS})

Automated vault->lab content sync (lane 219). ${md_count} markdown pages,
${file_count} files. Source: curated vault website/ subtree. See
scripts/sync-lab-content.sh in verdify-platform."

if [[ "$OPEN_PR" == true ]]; then
  git push -u origin "$BRANCH" --quiet
  gh pr create --repo "$LAB_REPO" --base "$LAB_BRANCH" --head "$BRANCH" \
    --title "chore(lab): vault->lab content sync ${TS}" \
    --body "$(cat <<EOF
Automated content refresh of the verdify-lab Quartz site (lane #219, part of #124).

- Source: curated vault \`website/\` subtree (the public subset).
- Snapshot: ${md_count} markdown pages, ${file_count} files.
- Generated by \`verdify-platform/scripts/sync-lab-content.sh\` running where the
  synced vault replica is reachable (GitHub CI cannot reach the vault).

On merge, \`.github/workflows/publish-lab-image.yml\` rebuilds
\`ghcr.io/verdifyconsultancy/verdify-lab\` from this snapshot and publishes a new
@sha256; the verdify-platform digest write-back PR (gated) then advances the
lab-site overlay pins.
EOF
)"
else
  echo "[sync-lab-content] committed on $BRANCH (no PR per --no-pr). Push manually:"
  echo "  git -C $WORKDIR/repo push -u origin $BRANCH"
fi

echo "[sync-lab-content] done."
