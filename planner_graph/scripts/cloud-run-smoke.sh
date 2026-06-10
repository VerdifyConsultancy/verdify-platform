#!/usr/bin/env bash

set -euo pipefail

: "${SERVICE_URL:?Set SERVICE_URL, e.g. https://planner-graph-xxxxx-uc.a.run.app}"

curl --fail --silent --show-error "${SERVICE_URL}/health"
