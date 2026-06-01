#!/usr/bin/env bash
# db-backup.sh — nightly Verdify DB dump to NFS (#24).
#
# Wraps the historical crontab one-liner
#   docker exec verdify-timescaledb pg_dump -U verdify -Fc verdify > <dest>
# behind the shared psql-verdify abstraction so the backup keeps working when
# the DB moves in-cluster (no docker socket). The DEFAULT backend is docker, so
# the emitted argv is byte-identical to the prior cron line on the live VM.
#
# Backend switch: VERDIFY_DB_BACKEND=docker|dsn (see scripts/lib/psql-verdify.sh).
#
# Env knobs (defaults preserve VM behavior):
#   VERDIFY_BACKUP_DIR   destination dir for the .dump   (default /mnt/iris/backups)
#
# The destination path keeps the YYYYMMDD date stamp the cron line used.

set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/psql-verdify.sh"

BACKUP_DIR="${VERDIFY_BACKUP_DIR:-/mnt/iris/backups}"
DEST="${BACKUP_DIR}/verdify-$(date +%Y%m%d).dump"

mkdir -p "$BACKUP_DIR"

# pg_dump custom format (-Fc), routed through the connection-prefix resolver.
# docker-exec mode -> docker exec verdify-timescaledb pg_dump -U verdify -d verdify -Fc
# dsn/direct mode  -> pg_dump -Fc   (PG* exported by the lib)
mapfile -t DUMP < <(verdify_pg_program_cmd pg_dump)
"${DUMP[@]}" -Fc > "$DEST"
