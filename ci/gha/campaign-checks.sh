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
# CAMPAIGN_SHARD=i/n (the workflow's campaign matrix, D-55): the tool's own sorted
# dashboard-db selection is filtered to every n-th file starting at i, so the n
# shards together run every file exactly once, each against its own private server
# as before. No file is removed (the files import each other). Unset runs all.
if [ -n "${CAMPAIGN_SHARD:-}" ]; then
  shard_i="${CAMPAIGN_SHARD%/*}" shard_n="${CAMPAIGN_SHARD#*/}"
  [[ "$shard_i" =~ ^[1-9][0-9]*$ && "$shard_n" =~ ^[1-9][0-9]*$ ]] && (( shard_i <= shard_n )) || {
    echo "invalid CAMPAIGN_SHARD=$CAMPAIGN_SHARD" >&2; exit 2; }
fi
export CI_TEST_REPORT="$HOME/ci-test-report.json"
# The registry's campaign-qualification command, unchanged.
export PYTHONPATH="$PWD" && uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv pip install --python .venv/bin/python -r ingestor/requirements-image.txt && python3 ci/gha/campaign_shard.py /usr/local/bin/verdify-campaign-ci
