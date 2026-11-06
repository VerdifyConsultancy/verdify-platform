#!/usr/bin/env bash
# GHA dashboard: the native `dashboard` check (jvallery/agents#5994) on the exact
# checkout, in the dashboard toolchain image (job image of the
# verdify-platform-ci-dashboard set; natively jvallery/weather-ci). Runs only when
# path impact sets dashboard-check; otherwise it passes as "not impacted", so the
# required context always reports.
#
# Classification runs in the runner container (python3); the checks run in the
# set's job-image sidecar through estate-ci sidecar-run.sh.
. "$(dirname "$0")/lib.sh"
ci_fetch_history
impact="$(mktemp -d)"
ci_classify "$impact" >/dev/null
if [ "$(cat "$impact/dashboard-check")" != true ]; then
  echo "path impact: dashboard-check=false; dashboard checks not required"
  exit 0
fi
ENV_NAMES="CI GITHUB_SHA GITHUB_REF GITHUB_REPOSITORY GITHUB_EVENT_NAME" TIMEOUT_MINUTES=45 \
  "$ESTATE_CI/sidecar-run.sh" job <<< "bash ci/gha/dashboard-checks.sh"
