#!/usr/bin/env bash
# The campaign job's checks, run inside its job image by ci/gha/campaign.sh.
. "$(dirname "$0")/lib.sh"
# An image built before the dashboard-db selection would silently run the legacy
# campaign set instead; refuse it until the agents-ci-tools pin moves.
grep -q VERDIFY_CAMPAIGN_SELECT /usr/local/bin/verdify-campaign-ci || {
  echo "agents-ci-tools predates VERDIFY_CAMPAIGN_SELECT; move its digest" >&2
  exit 1
}
export VERDIFY_CAMPAIGN_SELECT=dashboard-db
export CI_TEST_REPORT="$HOME/ci-test-report.json"
# The registry's campaign-qualification command, unchanged.
export PYTHONPATH="$PWD" && uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv pip install --python .venv/bin/python -r ingestor/requirements-image.txt && python3 /usr/local/bin/verdify-campaign-ci
