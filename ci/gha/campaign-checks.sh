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
# CAMPAIGN_SHARD=i/n (the workflow's campaign matrix, D-55): keep every n-th
# tests/test_dashboard_db_*.py file of the sorted list, starting at i, in this
# disposable checkout, so the n shards together run every file exactly once, each
# against its own private server as before. Unset runs all files.
if [ -n "${CAMPAIGN_SHARD:-}" ]; then
  shard_i="${CAMPAIGN_SHARD%/*}" shard_n="${CAMPAIGN_SHARD#*/}"
  [[ "$shard_i" =~ ^[1-9][0-9]*$ && "$shard_n" =~ ^[1-9][0-9]*$ ]] && (( shard_i <= shard_n )) || {
    echo "invalid CAMPAIGN_SHARD=$CAMPAIGN_SHARD" >&2; exit 2; }
  mapfile -t shard_files < <(find tests -maxdepth 1 -name 'test_dashboard_db_*.py' | LC_ALL=C sort)
  shard_kept=()
  for index in "${!shard_files[@]}"; do
    if (( index % shard_n == shard_i - 1 )); then
      shard_kept+=("${shard_files[$index]}")
    else
      rm -- "${shard_files[$index]}"
    fi
  done
  echo "campaign shard $shard_i/$shard_n: ${#shard_kept[@]} of ${#shard_files[@]} dashboard-db files: ${shard_kept[*]}"
  (( ${#shard_kept[@]} > 0 )) || { echo "campaign shard $CAMPAIGN_SHARD selected no files" >&2; exit 1; }
fi
export CI_TEST_REPORT="$HOME/ci-test-report.json"
# The registry's campaign-qualification command, unchanged.
export PYTHONPATH="$PWD" && uv sync --python 3.12 --extra dev --extra api --extra planner --extra planner-graph && uv pip install --python .venv/bin/python -r ingestor/requirements-image.txt && python3 /usr/local/bin/verdify-campaign-ci
