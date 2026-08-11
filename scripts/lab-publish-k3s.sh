#!/usr/bin/env bash
# lab-publish-k3s.sh - in-cluster lab.verdify.ai publisher.
#
# S3/object storage is the durable store:
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content/    Markdown + static source tree
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/public/     built Quartz output
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/state/      publish/build logs and context
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/manifests/  per-tree content-hash manifests
#
# The RWX PVC mounted at /work is only the local build/serve cache.
#
# Uploads go through scripts/s3-delta-sync.py: a per-file SHA-256 manifest gates
# the upload so an unchanged tree pushes nothing, and a changed tree pushes only
# the files whose content actually changed (NOT the whole ~400 MiB tree every run
# just because Quartz refreshed every mtime). See docs/site-publishing-pipeline.md.
set -euo pipefail

ORIGINAL_ARGS=("$@")
: "${LAB_S3_BUCKET:?set LAB_S3_BUCKET in the verdify-lab-publisher-s3 Secret}"

PATH="/opt/venv/bin:${PATH}"
export PATH
export PYTHONPATH="${PYTHONPATH:-/app/ingestor:/app}"
export VERDIFY_DB_BACKEND="${VERDIFY_DB_BACKEND:-dsn}"
export VERDIFY_PSQL_MODE="${VERDIFY_PSQL_MODE:-direct}"
export VERDIFY_SCRIPT_ROOT="${VERDIFY_SCRIPT_ROOT:-/app/scripts}"
export PYTHON="${PYTHON:-/opt/venv/bin/python}"
export LAB_LOCAL_TIMEZONE="${LAB_LOCAL_TIMEZONE:-America/Denver}"
export TZ="$LAB_LOCAL_TIMEZONE"

LAB_S3_PREFIX="${LAB_S3_PREFIX:-lab}"
LAB_S3_PREFIX="${LAB_S3_PREFIX#/}"
LAB_S3_PREFIX="${LAB_S3_PREFIX%/}"

CONTENT_URI="${LAB_S3_CONTENT_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/content}"
PUBLIC_URI="${LAB_S3_PUBLIC_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/public}"
STATE_URI="${LAB_S3_STATE_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/state}"
# Per-tree content-hash manifests live OUTSIDE content/public/state so they never
# feed back into the delta walk; see scripts/s3-delta-sync.py.
MANIFEST_URI="${LAB_S3_MANIFEST_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/manifests}"
ENDPOINT_URL="${LAB_S3_ENDPOINT_URL:-${AWS_ENDPOINT_URL:-}}"

WORK_ROOT="${LAB_WORK_ROOT:-/work/publisher}"
CONTENT_DIR="${WORK_ROOT}/content"
PUBLIC_DIR="${WORK_ROOT}/public"
STATE_DIR="${WORK_ROOT}/state"
BUILD_ROOT="${WORK_ROOT}/builds"
LOCK_DIR="${WORK_ROOT}/locks"
MANIFEST_DIR="${WORK_ROOT}/manifests"
SITE_RUNTIME="${LAB_SITE_RUNTIME:-/opt/verdify-site}"
PUBLISH_SCRIPT="${LAB_PUBLISH_SCRIPT:-/app/scripts/publish-site-content.sh}"
SITE_CONTENT_LINK="${LAB_SITE_CONTENT_LINK:-/srv/verdify/verdify-site/content}"
STATE_LINK="${LAB_STATE_LINK:-/srv/verdify/state}"
REPO_LINK="${LAB_REPO_LINK:-/mnt/iris/verdify}"
VAULT_LINK="${LAB_VAULT_LINK:-/mnt/iris/verdify-vault/website}"

DATE_ARG="${1:-${LAB_PUBLISH_DATE:-$(date +%Y-%m-%d)}}"
REASON="${LAB_PUBLISH_REASON:-k3s-publisher}"

reason_class() {
  case "$1" in
    "") printf 'unspecified' ;;
    manual) printf 'manual' ;;
    k3s-publisher|cron|scheduled*) printf 'scheduled' ;;
    *planner*|plan-*) printf 'planner' ;;
    *forecast*) printf 'forecast' ;;
    *) printf 'custom' ;;
  esac
}

REASON_CLASS="$(reason_class "$REASON")"
WRAPPER_LOCK="${LAB_PUBLISH_WRAPPER_LOCK:-$LOCK_DIR/publish-wrapper.lock}"
WRAPPER_LOCKED_RC="${LAB_PUBLISH_WRAPPER_LOCKED_RC:-75}"
CACHE_PYTHON="${VERDIFY_CACHE_PYTHON:-python3}"
CACHE_LOCK_HELPER="${VERDIFY_CACHE_LOCK_HELPER:-/usr/local/bin/prepare-lab-cache-lock}"
if [[ ! -f "$CACHE_LOCK_HELPER" && -f "$VERDIFY_SCRIPT_ROOT/prepare-lab-cache-lock.py" ]]; then
  CACHE_LOCK_HELPER="$VERDIFY_SCRIPT_ROOT/prepare-lab-cache-lock.py"
fi

# Serialize the entire S3 -> candidate -> publish -> S3 flow on the exact same
# descriptor-safe lock used by cache initialization.  The helper creates and
# mode-normalizes the final ROOT component, opens ROOT/locks/publish-wrapper.lock
# relative to held nofollow descriptors, then carries the flocked regular-file
# fd across this script's exec.
if [[ "$WRAPPER_LOCK" != "$LOCK_DIR/publish-wrapper.lock" ]]; then
  echo "lab publisher cache lock configuration failed" >&2
  exit 2
fi
if [[ ! -f "$CACHE_LOCK_HELPER" ]]; then
  echo "Lab cache lock initialization failed" >&2
  exit 2
fi
if [[ "${VERDIFY_CACHE_LOCK_HELD_FD:-}" != "8" ]]; then
  exec "$CACHE_PYTHON" "$CACHE_LOCK_HELPER" \
    --root "$WORK_ROOT" \
    --fd 8 \
    --nonblocking \
    --busy-exit-code "$WRAPPER_LOCKED_RC" \
    --busy-message "k3s lab publish already running; skipping reason_class=${REASON_CLASS}" \
    -- \
    bash "$0" "${ORIGINAL_ARGS[@]}"
fi
if ! "$CACHE_PYTHON" "$CACHE_LOCK_HELPER" --root "$WORK_ROOT" --fd 8 --verify-held >/dev/null 2>&1; then
  echo "Lab cache lock initialization failed" >&2
  exit 2
fi

aws_s3() {
  if [[ -n "$ENDPOINT_URL" ]]; then
    aws --endpoint-url "$ENDPOINT_URL" s3 "$@"
  else
    aws s3 "$@"
  fi
}

# delta_sync LABEL LOCAL_DIR REMOTE_URI — content-hash, snapshot-protected upload.
# Uploads only files whose SHA-256 changed and skips entirely when nothing
# changed, so the every-10-min rebuild no longer re-pushes the whole tree to the
# (HDD-backed) endpoint just because Quartz refreshed every mtime.
delta_sync() {
  "$PYTHON" "${VERDIFY_SCRIPT_ROOT}/s3-delta-sync.py" \
    --label "$1" \
    --local "$2" \
    --remote "$3" \
    --manifest "${MANIFEST_URI}/$1.json" \
    --local-manifest "${MANIFEST_DIR}/$1.json" \
    --endpoint "$ENDPOINT_URL" \
    --delete
}

mkdir -p "$CONTENT_DIR" "$PUBLIC_DIR" "$STATE_DIR" "$BUILD_ROOT" "$LOCK_DIR" "$MANIFEST_DIR"

CONTENT_LIST="${STATE_DIR}/s3-content-list.tmp"
if aws_s3 ls "${CONTENT_URI}/" >"$CONTENT_LIST" 2>/dev/null && [[ -s "$CONTENT_LIST" ]]; then
  echo "Syncing lab content source from ${CONTENT_URI}/"
  aws_s3 sync "${CONTENT_URI}/" "${CONTENT_DIR}/" --delete --exact-timestamps
elif find "$CONTENT_DIR" -name '*.md' -print -quit | grep -q .; then
  echo "S3 content prefix is empty/unreadable; using existing PVC content cache."
else
  cat >&2 <<EOF
No lab content source found.
Seed ${CONTENT_URI}/ with the website Markdown/static tree before enabling the
k3s publisher. Example from a trusted workstation:
  aws s3 sync /Users/jason/Iris/verdify-vault/website/ ${CONTENT_URI}/ --delete
EOF
  exit 2
fi
rm -f "$CONTENT_LIST"

if ! find "$CONTENT_DIR" -name '*.md' -print -quit | grep -q .; then
  echo "Lab content source contains no Markdown files: ${CONTENT_DIR}" >&2
  exit 2
fi

mkdir -p \
  "$(dirname "$SITE_CONTENT_LINK")" \
  "$(dirname "$STATE_LINK")" \
  "$(dirname "$REPO_LINK")" \
  "$(dirname "$VAULT_LINK")"
ln -sfn "$CONTENT_DIR" "$SITE_CONTENT_LINK"
ln -sfn "$STATE_DIR" "$STATE_LINK"
ln -sfn /app "$REPO_LINK"
ln -sfn "$CONTENT_DIR" "$VAULT_LINK"
ln -sfn "$CONTENT_DIR" "${SITE_RUNTIME}/content"

export VERDIFY_SITE_SOURCE="${VERDIFY_SITE_SOURCE:-/app/site}"
export VERDIFY_SITE_RUNTIME="$SITE_RUNTIME"
export VERDIFY_SITE_PUBLIC="${VERDIFY_SITE_PUBLIC:-$PUBLIC_DIR}"
export VERDIFY_PUBLIC_CONTENT_ROOT="${VERDIFY_PUBLIC_CONTENT_ROOT:-$CONTENT_DIR}"
export VERDIFY_SITE_BUILD_ROOT="${VERDIFY_SITE_BUILD_ROOT:-$BUILD_ROOT}"
export VERDIFY_PUBLISH_LOG="${VERDIFY_PUBLISH_LOG:-$STATE_DIR/publish.log}"
export VERDIFY_PUBLISH_LOCK="${VERDIFY_PUBLISH_LOCK:-$LOCK_DIR/publish.lock}"
export VERDIFY_SITE_BUILD_LOG="${VERDIFY_SITE_BUILD_LOG:-$STATE_DIR/site-build.log}"
export VERDIFY_SITE_BUILD_LOCK="${VERDIFY_SITE_BUILD_LOCK:-$LOCK_DIR/site-build.lock}"
export VERDIFY_SITE_BUILD_LOCKED_RC="${VERDIFY_SITE_BUILD_LOCKED_RC:-75}"
export VERDIFY_SITE_BUILD_MARKER="${VERDIFY_SITE_BUILD_MARKER:-$STATE_DIR/site-build-last-run}"
export VERDIFY_SITE_CONTAINER="${VERDIFY_SITE_CONTAINER:-}"
# In k3s, a lock-skipped publish must not proceed to S3 sync; a manual job can
# overlap a scheduled job even though the CronJob itself uses Forbid.
export VERDIFY_PUBLISH_LOCKED_RC="${VERDIFY_PUBLISH_LOCKED_RC:-75}"
export LOG="${LOG:-$STATE_DIR/site-build.log}"
export PGHOST="${PGHOST:-${DB_HOST:-verdify-db}}"
export PGPORT="${PGPORT:-${DB_PORT:-5432}}"
export PGDATABASE="${PGDATABASE:-${DB_NAME:-verdify}}"
export PGUSER="${PGUSER:-${DB_USER:-verdify}}"
if [[ -z "${PGPASSWORD:-}" ]]; then
  export PGPASSWORD="${POSTGRES_PASSWORD:-${DB_PASS:-}}"
fi
export DB_DSN="${DB_DSN:-postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}}"
export VERDIFY_DSN="${VERDIFY_DSN:-$DB_DSN}"
export VERDIFY_DB_DSN="${VERDIFY_DB_DSN:-$DB_DSN}"
export DATABASE_URL="${DATABASE_URL:-$DB_DSN}"
export VERDIFY_DAILY_PLAN_DB_CMD="${VERDIFY_DAILY_PLAN_DB_CMD:-psql -U ${PGUSER} -d ${PGDATABASE} -t -A}"

echo "Starting k3s lab publish: date=${DATE_ARG} reason_class=${REASON_CLASS}"
publish_rc=0
"$PUBLISH_SCRIPT" --date "$DATE_ARG" --reason "$REASON" || publish_rc=$?

if [[ "$publish_rc" -ne 0 ]]; then
  echo "Lab publish failed (rc=${publish_rc}); syncing state only" >&2
  if ! delta_sync state "$STATE_DIR" "$STATE_URI"; then
    echo "State sync also failed after publish failure; local state remains available" >&2
  fi
  exit "$publish_rc"
fi

echo "Publishing content-hash deltas to object storage (skips unchanged trees)"
delta_sync content "$CONTENT_DIR" "$CONTENT_URI"
delta_sync public "$PUBLIC_DIR" "$PUBLIC_URI"
delta_sync state "$STATE_DIR" "$STATE_URI"

echo "k3s lab publish complete: date=${DATE_ARG} reason_class=${REASON_CLASS}"
