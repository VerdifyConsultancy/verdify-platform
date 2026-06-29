#!/usr/bin/env bash
# Compatibility shim for VM-era generators that still call:
#   docker exec [-i] [-e PGOPTIONS=...] verdify-timescaledb psql ...
#
# The k3s lab publisher has no Docker socket. Route those calls to the local
# psql client, using PG* env provided by the CronJob.
set -euo pipefail

if [[ "${1:-}" != "exec" ]]; then
  echo "lab-publisher docker shim only supports 'docker exec ... psql'" >&2
  exit 2
fi
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    -i|-t)
      shift
      ;;
    -e)
      if [[ "${2:-}" == *=* ]]; then
        export "$2"
      fi
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "lab-publisher docker shim ignoring unsupported docker flag: $1" >&2
      shift
      ;;
    *)
      # Container name, historically verdify-timescaledb.
      shift
      break
      ;;
  esac
done

if [[ "${1:-}" != "psql" ]]; then
  echo "lab-publisher docker shim expected psql command, got: ${1:-<empty>}" >&2
  exit 2
fi

exec "$@"
