#!/bin/sh
set -eu

case "${1:-}" in
  init|reconcile)
    test "$#" -eq 1
    exec node /app/release-runtime/reconcile.mjs "$1"
    ;;
  *)
    echo "usage: release-runtime-entrypoint init|reconcile" >&2
    exit 64
    ;;
esac
