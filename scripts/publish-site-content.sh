#!/usr/bin/env bash
# publish-site-content.sh — regenerate all generated lab.verdify.ai content and rebuild.
#
# This is the single production entry point for public site refreshes. Planner
# publishes, forecast refreshes, and manual full refreshes should call this
# script instead of invoking individual page generators directly.
set -euo pipefail

DATE=$(date +%Y-%m-%d)
REASON="manual"
REBUILD=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --date)
      DATE="$2"
      shift 2
      ;;
    --reason)
      REASON="$2"
      shift 2
      ;;
    --no-rebuild)
      REBUILD=false
      shift
      ;;
    -h|--help)
      cat <<'EOF'
Usage: publish-site-content.sh [--date YYYY-MM-DD] [--reason NAME] [--no-rebuild]

Regenerates generated public content, updates planner static context, and
rebuilds the Quartz site unless --no-rebuild is provided.
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

SCRIPT_ROOT=${VERDIFY_SCRIPT_ROOT:-/srv/verdify/scripts}
PYTHON=${PYTHON:-/srv/greenhouse/.venv/bin/python}
LOG=${VERDIFY_PUBLISH_LOG:-/srv/verdify/state/publish.log}
LOCK=${VERDIFY_PUBLISH_LOCK:-/var/lock/verdify-site-content-publish.lock}
PREV_DATE=$(date -d "${DATE} -1 day" +%Y-%m-%d)

mkdir -p "$(dirname "$LOG")" "$(dirname "$LOCK")"

run_step() {
  echo "[$(date -Is)] $*" | tee -a "$LOG"
  "$@" 2>&1 | tee -a "$LOG"
}

{
  flock -n 9 || {
    echo "[$(date -Is)] publish already running; skipping ${REASON} refresh" | tee -a "$LOG"
    exit 0
  }

  echo "[$(date -Is)] Starting site content publish: reason=${REASON}, date=${DATE}" | tee -a "$LOG"

  run_step "$PYTHON" "$SCRIPT_ROOT/generate-daily-plan.py" --date "$PREV_DATE"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-daily-plan.py" --date "$DATE"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-forecast-page.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-plans-index.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-lessons-page.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-ai-tunables-page.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/generate-baseline-vs-iris-page.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/update-evidence-snapshots.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/render-equipment-page.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/render-zone-pages.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/render-crop-profiles.py"
  run_step "$PYTHON" "$SCRIPT_ROOT/export-hourly-performance-dataset.py"
  run_step bash "$SCRIPT_ROOT/export-public-sample-dataset.sh"
  run_step bash "$SCRIPT_ROOT/gather-static-context.sh"

  if [[ "$REBUILD" == true ]]; then
    run_step "$SCRIPT_ROOT/rebuild-site.sh"
    echo "[$(date -Is)] Site content publish complete: reason=${REASON}, date=${DATE}" | tee -a "$LOG"
  else
    echo "[$(date -Is)] Site content generated without rebuild: reason=${REASON}, date=${DATE}" | tee -a "$LOG"
  fi
} 9>"$LOCK"
