#!/usr/bin/env bash
# lab-publish-k3s.sh - in-cluster lab.verdify.ai publisher.
#
# S3/object storage is the durable store:
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/content/  Markdown + static source tree
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/public/   built Quartz output
#   s3://$LAB_S3_BUCKET/$LAB_S3_PREFIX/state/    publish/build logs and context
#
# The RWX PVC mounted at /work is only the local build/serve cache.
set -euo pipefail

: "${LAB_S3_BUCKET:?set LAB_S3_BUCKET in the verdify-lab-publisher-s3 Secret}"

PATH="/opt/venv/bin:${PATH}"
export PATH
export PYTHONPATH="${PYTHONPATH:-/app/ingestor:/app}"
export VERDIFY_DB_BACKEND="${VERDIFY_DB_BACKEND:-dsn}"
export VERDIFY_PSQL_MODE="${VERDIFY_PSQL_MODE:-direct}"
export VERDIFY_SCRIPT_ROOT="${VERDIFY_SCRIPT_ROOT:-/app/scripts}"
export PYTHON="${PYTHON:-/opt/venv/bin/python}"

LAB_S3_PREFIX="${LAB_S3_PREFIX:-lab}"
LAB_S3_PREFIX="${LAB_S3_PREFIX#/}"
LAB_S3_PREFIX="${LAB_S3_PREFIX%/}"

CONTENT_URI="${LAB_S3_CONTENT_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/content}"
PUBLIC_URI="${LAB_S3_PUBLIC_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/public}"
STATE_URI="${LAB_S3_STATE_URI:-s3://${LAB_S3_BUCKET}/${LAB_S3_PREFIX}/state}"
ENDPOINT_URL="${LAB_S3_ENDPOINT_URL:-${AWS_ENDPOINT_URL:-}}"

WORK_ROOT="${LAB_WORK_ROOT:-/work}"
CONTENT_DIR="${WORK_ROOT}/content"
PUBLIC_DIR="${WORK_ROOT}/public"
STATE_DIR="${WORK_ROOT}/state"
BUILD_ROOT="${WORK_ROOT}/builds"
LOCK_DIR="${WORK_ROOT}/locks"
SITE_RUNTIME="${LAB_SITE_RUNTIME:-/opt/verdify-site}"

DATE_ARG="${1:-${LAB_PUBLISH_DATE:-$(date +%Y-%m-%d)}}"
REASON="${LAB_PUBLISH_REASON:-k3s-publisher}"

aws_s3() {
  if [[ -n "$ENDPOINT_URL" ]]; then
    aws --endpoint-url "$ENDPOINT_URL" s3 "$@"
  else
    aws s3 "$@"
  fi
}

mkdir -p "$CONTENT_DIR" "$PUBLIC_DIR" "$STATE_DIR" "$BUILD_ROOT" "$LOCK_DIR"

CONTENT_LIST="${STATE_DIR}/s3-content-list.tmp"
if aws_s3 ls "${CONTENT_URI}/" >"$CONTENT_LIST" 2>/dev/null && [[ -s "$CONTENT_LIST" ]]; then
  echo "Syncing lab content source from ${CONTENT_URI}/"
  aws_s3 sync "${CONTENT_URI}/" "${CONTENT_DIR}/" --delete
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

mkdir -p /srv/verdify/verdify-site /srv/verdify /mnt/iris/verdify-vault
ln -sfn "$CONTENT_DIR" /srv/verdify/verdify-site/content
ln -sfn "$STATE_DIR" /srv/verdify/state
ln -sfn /app /mnt/iris/verdify
ln -sfn "$CONTENT_DIR" /mnt/iris/verdify-vault/website
ln -sfn "$CONTENT_DIR" "${SITE_RUNTIME}/content"

export VERDIFY_SITE_SOURCE="${VERDIFY_SITE_SOURCE:-/app/site}"
export VERDIFY_SITE_RUNTIME="$SITE_RUNTIME"
export VERDIFY_SITE_PUBLIC="${VERDIFY_SITE_PUBLIC:-$PUBLIC_DIR}"
export VERDIFY_SITE_BUILD_ROOT="${VERDIFY_SITE_BUILD_ROOT:-$BUILD_ROOT}"
export VERDIFY_PUBLISH_LOG="${VERDIFY_PUBLISH_LOG:-$STATE_DIR/publish.log}"
export VERDIFY_PUBLISH_LOCK="${VERDIFY_PUBLISH_LOCK:-$LOCK_DIR/publish.lock}"
export VERDIFY_SITE_BUILD_LOG="${VERDIFY_SITE_BUILD_LOG:-$STATE_DIR/site-build.log}"
export VERDIFY_SITE_BUILD_LOCK="${VERDIFY_SITE_BUILD_LOCK:-$LOCK_DIR/site-build.lock}"
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

echo "Starting k3s lab publish: date=${DATE_ARG} reason=${REASON}"
/app/scripts/publish-site-content.sh --date "$DATE_ARG" --reason "$REASON"

echo "Uploading generated content to ${CONTENT_URI}/"
aws_s3 sync "${CONTENT_DIR}/" "${CONTENT_URI}/" --delete
echo "Uploading built public site to ${PUBLIC_URI}/"
aws_s3 sync "${PUBLIC_DIR}/" "${PUBLIC_URI}/" --delete
echo "Uploading publish state to ${STATE_URI}/"
aws_s3 sync "${STATE_DIR}/" "${STATE_URI}/" --delete

echo "k3s lab publish complete: date=${DATE_ARG} reason=${REASON}"
