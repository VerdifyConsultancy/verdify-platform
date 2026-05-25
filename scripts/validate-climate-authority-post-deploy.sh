#!/usr/bin/env bash
# Verify the ClimateIntent authority path after merge/service restart and before OTA.
#
# This script does not restart services and does not flash firmware. It proves
# that the deployed service path is producing fresh, graphable controller-proof
# rows before firmware-deploy is allowed to compile/upload.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-/srv/greenhouse/.venv/bin/python}"
API_HEALTH_URL="${API_HEALTH_URL:-http://127.0.0.1:8300/health}"

cd "$REPO_ROOT"

echo "== Climate authority post-deploy proof =="
echo "Repository: $REPO_ROOT"
echo

echo "[1/5] Shell health, including climate_action_log freshness/completeness"
bash scripts/health-check.sh
echo

echo "[2/5] API /health controller-proof status"
curl -fsS "$API_HEALTH_URL" | "$PYTHON_BIN" -c '
import json
import sys

payload = json.load(sys.stdin)
checks = payload.get("checks") or {}
failures = []
status = payload.get("status")
proof_missing = checks.get("climate_action_log_proof_missing")
service_status = checks.get("service_climate_action_log")

if status != "ok":
    failures.append(f"API status={status!r}")

age = checks.get("climate_action_log_age_seconds")
if not isinstance(age, (int, float)) or age >= 300:
    failures.append(f"climate_action_log_age_seconds={age!r}")

if "climate_action_log_proof_missing" not in checks:
    failures.append("API /health lacks climate_action_log_proof_missing; restart/deploy verdify-api")
elif proof_missing:
    failures.append(f"climate_action_log_proof_missing={proof_missing!r}")

if service_status != "ok":
    failures.append(f"service_climate_action_log={service_status!r}")

if failures:
    print("✗ API health controller-proof check failed: " + "; ".join(failures), file=sys.stderr)
    sys.exit(1)

print("✓ API health controller-proof check passed")
'
echo

echo "[3/5] Active plan coverage"
bash scripts/validate-plan-coverage.sh
echo

echo "[4/5] ClimateIntent contract audit"
"$PYTHON_BIN" scripts/audit-climate-intent-contract.py
echo

echo "[5/5] Firmware OTA preflight gates"
bash scripts/firmware-deploy-preflight.sh
echo

echo "✓ Climate authority post-deploy proof passed"
