#!/usr/bin/env bash
# GHA campaign: the native `campaign` check (jvallery/agents#5994):
# tests/test_dashboard_db_*.py, each against its own private PostgreSQL 16 with
# TimescaleDB and pgvector, through verdify-campaign-ci from the agents-ci-tools
# image (job image of the verdify-platform-ci-tools set). Runs only when path
# impact sets dashboard-db; otherwise it passes as "not impacted".
#
# Classification runs in the runner container (python3); the checks run in the
# set's job-image sidecar through estate-ci sidecar-run.sh.
. "$(dirname "$0")/lib.sh"
ci_fetch_history
impact="$(mktemp -d)"
ci_classify "$impact" >/dev/null
if [ "$(cat "$impact/dashboard-db")" != true ]; then
  echo "path impact: dashboard-db=false; campaign checks not required"
  exit 0
fi
ENV_NAMES="CI GITHUB_SHA GITHUB_REF GITHUB_REPOSITORY GITHUB_EVENT_NAME CAMPAIGN_SHARD" TIMEOUT_MINUTES=45 \
  "$ESTATE_CI/sidecar-run.sh" job <<< "bash ci/gha/campaign-checks.sh"
