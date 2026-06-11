#!/usr/bin/env bash
# Direct psql access to the Verdify TimescaleDBs from any kubectl host
# (laptop-root's MacBook, an agent pod, CI) — no VPN, no LB exposure, no
# credentials on disk. Two modes:
#
#   scripts/verdify-db.sh [prod|dev] [psql args...]
#       kubectl-exec psql inside the verdify-db-0 pod (auth happens in-pod;
#       no password ever leaves the cluster). Interactive when run from a TTY
#       with no args, one-shot otherwise:
#         scripts/verdify-db.sh prod -c "SELECT count(*) FROM climate;"
#         scripts/verdify-db.sh dev   -t -A -c "SELECT max(ts) FROM climate;"
#
#   scripts/verdify-db.sh [prod|dev] --tunnel [localport]
#       Foreground kubectl port-forward for tools that need a real TCP socket
#       (psycopg/pandas/DBeaver/Grafana-dev). Default localport 5433 (prod) /
#       5434 (dev). kubectl port-forward rides the kubelet/API-server channel,
#       so the in-cluster default-deny NetworkPolicies do not apply to it.
#       Fetch the password into your client env WITHOUT printing it:
#         export PGPASSWORD="$(kubectl -n verdify-prod get secret \
#           verdify-app-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
#
# The DB identity is uniform across envs: db=verdify user=verdify, password in
# k8s Secret verdify-app-secrets/POSTGRES_PASSWORD (per-namespace). The dev DB
# is a nightly prod-restored copy (overlays/dev/db-restore-from-prod.yaml) —
# prefer dev for heavy/exploratory analysis; prod queries share the box with
# the live greenhouse write path.
#
# Related: scripts/lib/psql-verdify.sh (VERDIFY_DB_BACKEND=kube) is the same
# transport the firmware-deploy preflight uses; this wrapper is the
# human/analysis front-end.
set -euo pipefail

usage() {
  echo "usage: $0 [prod|dev] [--tunnel [localport] | psql args...]" >&2
  exit 2
}

ENV="prod"
case "${1:-}" in
  prod|dev) ENV="$1"; shift ;;
  -h|--help) usage ;;
esac
NS="verdify-${ENV}"

if [ "${1:-}" = "--tunnel" ]; then
  if [ -n "${2:-}" ]; then PORT="$2"; elif [ "$ENV" = "prod" ]; then PORT=5433; else PORT=5434; fi
  echo "Port-forwarding ${NS}/svc/verdify-db -> localhost:${PORT}  (Ctrl-C to stop)" >&2
  echo "Connect:  psql -h localhost -p ${PORT} -U verdify -d verdify" >&2
  echo "Password: export PGPASSWORD=\"\$(kubectl -n ${NS} get secret verdify-app-secrets -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)\"" >&2
  exec kubectl -n "$NS" port-forward svc/verdify-db "${PORT}:5432"
fi

TTY_FLAGS=()
if [ -t 0 ] && [ -t 1 ]; then
  TTY_FLAGS=(-it)
elif [ ! -t 0 ]; then
  TTY_FLAGS=(-i)
fi
exec kubectl exec "${TTY_FLAGS[@]+"${TTY_FLAGS[@]}"}" -n "$NS" verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify "$@"
