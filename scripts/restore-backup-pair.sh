#!/usr/bin/env bash
# Restore one verified backup pair into a disposable, socket-only PostgreSQL.
set -Eeuo pipefail
umask 077

: "${BACKUP_DIR:?}"
: "${BACKUP_STEM:?}"
: "${PGDATA:?}"
: "${PGHOST:?}"
: "${PGPORT:?}"
: "${PGDATABASE:?}"
: "${VERIFY_SCRIPT:?}"
: "${AUDIT_SQL:?}"
: "${OWNERSHIP_SQL:?}"
: "${OWNER_REPAIR_TEST_SQL:?}"
: "${RESTORED_OWNER_TEST_SQL:?}"
if [ "${PGDATABASE}" != verdify_rehearsal ]; then
  echo "[restore-pair] FATAL: disposable database name required" >&2
  exit 1
fi
pair_metadata="$("${VERIFY_SCRIPT}" "${BACKUP_DIR}" "${BACKUP_STEM}")"
IFS='|' read -r source_database owner <<< "${pair_metadata}"
if [ "${source_database}" != verdify ] || [ -z "${owner}" ]; then
  echo "[restore-pair] FATAL: unexpected backup source database or owner" >&2
  exit 1
fi
export PGUSER="${owner}"
dump="${BACKUP_DIR}/${BACKUP_STEM}.dump"
roles="${BACKUP_DIR}/${BACKUP_STEM}.roles.sql"
echo "[restore-pair] verified backup=${BACKUP_STEM} dump_sha256=$(sha256sum -- "${dump}" | awk '{print $1}')"

# The CNPG mode connects only to the already-owned local primary postmaster.
# Its caller must first bind the exact Kubernetes Cluster/Pod UID and image;
# these inner guards never permit the production database or a remote DSN.
if [ "${RESTORE_SERVER_MODE:-standalone}" = cnpg ]; then
  if [ "$(id -u)" != 26 ] || [ "${PGHOST}" != /controller/run ] || [ "${PGPORT}" != 5432 ] \
      || [ "${owner}" != verdify ] \
      || [ "${CNPG_ROLE_HELPER:-}" != /var/lib/postgresql/data/restore-custody/scripts/cnpg-restore-role-parity.py ] \
      || [ "${CNPG_ACL_HELPER:-}" != /var/lib/postgresql/data/restore-custody/scripts/cnpg-source-database-acl.py ]; then
    echo '[restore-pair] FATAL: CNPG local rehearsal identity required' >&2
    exit 1
  fi
  export PGUSER=postgres
  psql -X -v ON_ERROR_STOP=1 -d postgres <<'SQL' >/dev/null
DO $guard$ BEGIN
 IF current_setting('cluster_name')<>'verdify-cnpg-rehearsal'
    OR current_setting('server_version_num')::int<>160013
    OR pg_is_in_recovery()
    OR EXISTS(SELECT 1 FROM pg_database WHERE datname='verdify_rehearsal')
    OR EXISTS(SELECT 1 FROM pg_database WHERE datname NOT IN
               ('postgres','template0','template1','rehearsal_bootstrap')) THEN
   RAISE EXCEPTION 'CNPG import refuses target identity or nonempty cluster';
 END IF;
END $guard$;
SQL
  pg_dumpall --roles-only --no-role-passwords --no-comments --no-security-labels \
    > /tmp/roles.before.sql 2>/tmp/roles-before.stderr
  python3 "${CNPG_ROLE_HELPER}" --source "${roles}" --current /tmp/roles.before.sql \
    --replay /tmp/roles.replay.sql
elif [ "${RESTORE_SERVER_MODE:-standalone}" = standalone ]; then
# The TimescaleDB image can run as backup-plane uid 999 without a passwd entry.
if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
  printf 'postgres:x:%s:%s:postgres:/tmp:/bin/sh\n' "$(id -u)" "$(id -g)" > /tmp/nss_passwd
  printf 'postgres:x:%s:\n' "$(id -g)" > /tmp/nss_group
  export LD_PRELOAD=/usr/lib/libnss_wrapper.so
  export NSS_WRAPPER_PASSWD=/tmp/nss_passwd
  export NSS_WRAPPER_GROUP=/tmp/nss_group
fi

# initdb needs one bootstrap superuser. It is the manifest's database owner;
# skip exactly its CREATE ROLE line while replaying the real roles artifact.
initdb -D "${PGDATA}" --username="${owner}" --auth-local=trust --auth-host=reject >/dev/null
pg_ctl -D "${PGDATA}" \
  -o "-k ${PGHOST} -p ${PGPORT} -c listen_addresses='' -c shared_preload_libraries=timescaledb -c timescaledb.telemetry_level=off" \
  -w start >/dev/null
cleanup() {
  pg_ctl -D "${PGDATA}" -m fast -w stop >/dev/null 2>&1 || true
}
trap cleanup EXIT
awk -v bootstrap="CREATE ROLE ${owner};" '$0 != bootstrap { print }' "${roles}" > /tmp/roles.replay.sql
else
  echo '[restore-pair] FATAL: unsupported server mode' >&2
  exit 1
fi
if ! psql -X -v ON_ERROR_STOP=1 -d postgres -f /tmp/roles.replay.sql \
    >/tmp/roles.stdout 2>/tmp/roles.stderr; then
  echo "[restore-pair] FATAL: role replay failed; raw SQL output withheld" >&2
  exit 1
fi
createdb -O "${owner}" "${PGDATABASE}"
if [ "${RESTORE_SERVER_MODE:-standalone}" = cnpg ]; then
  python3 "${CNPG_ACL_HELPER}" --source "${CNPG_SOURCE_WITNESS:?}" \
    --sha256 "${CNPG_SOURCE_WITNESS_SHA256:?}" --output /tmp/source-database-acl.sql
  psql -X -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f /tmp/source-database-acl.sql >/dev/null
fi
psql -X -v ON_ERROR_STOP=1 -d "${PGDATABASE}" \
  -c "SET ROLE \"${owner}\"; CREATE EXTENSION IF NOT EXISTS timescaledb" >/dev/null
psql -X -v ON_ERROR_STOP=1 -d "${PGDATABASE}" \
  -c 'SELECT timescaledb_pre_restore()' >/dev/null
pg_restore -l "${dump}" > /tmp/restore.toc
grep -v 'MATERIALIZED VIEW DATA' /tmp/restore.toc > /tmp/restore.toc.filtered
if ! pg_restore --exit-on-error --role "${owner}" \
    --use-list=/tmp/restore.toc.filtered --dbname="${PGDATABASE}" "${dump}" \
    >/tmp/restore.stdout 2>/tmp/restore.stderr; then
  echo "[restore-pair] FATAL: database restore failed; raw SQL output withheld" >&2
  exit 1
fi
psql -X -v ON_ERROR_STOP=1 -d "${PGDATABASE}" \
  -c 'SELECT timescaledb_post_restore()' >/dev/null

# Inspect every Timescale-managed regular and compressed chunk against its
# logical hypertable before any migration replay. Parent ALTER/REASSIGN is the
# supported owner repair path; direct ALTER of an internal chunk is not.
psql -X -qAt -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f "${OWNERSHIP_SQL}"
psql -X -qAt -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f "${OWNER_REPAIR_TEST_SQL}"
psql -X -qAt -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f "${RESTORED_OWNER_TEST_SQL}"

# pg_restore's hardened empty search_path can prevent dependent matviews from
# refreshing in archive order. Refresh them afterward, to a bounded fixed point.
remaining="$(psql -X -qAt -d "${PGDATABASE}" \
  -c 'SELECT count(*) FROM pg_matviews WHERE NOT ispopulated')"
round=0
while [ "${remaining}" -gt 0 ]; do
  round=$((round + 1))
  if [ "${round}" -gt 10 ]; then
    echo "[restore-pair] FATAL: materialized views did not populate" >&2
    exit 1
  fi
  progressed=0
  while IFS= read -r statement; do
    [ -n "${statement}" ] || continue
    if psql -X -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -c "${statement}" \
        >/tmp/refresh.stdout 2>/tmp/refresh.stderr; then
      progressed=1
    fi
  done < <(psql -X -qAt -d "${PGDATABASE}" \
    -c "SELECT format('REFRESH MATERIALIZED VIEW %I.%I;', schemaname, matviewname) FROM pg_matviews WHERE NOT ispopulated")
  remaining="$(psql -X -qAt -d "${PGDATABASE}" \
    -c 'SELECT count(*) FROM pg_matviews WHERE NOT ispopulated')"
  if [ "${progressed}" -eq 0 ] && [ "${remaining}" -gt 0 ]; then
    echo "[restore-pair] FATAL: materialized view refresh stalled" >&2
    exit 1
  fi
done

# Re-export the normalized role catalog from the restored cluster. pg_dumpall
# generates a fresh random psql \restrict/\unrestrict key for every invocation;
# remove only those two transport lines before the byte-for-byte comparison.
if ! pg_dumpall --roles-only --no-role-passwords --no-comments --no-security-labels \
    -h "${PGHOST}" -p "${PGPORT}" -U "${owner}" -l postgres \
    > /tmp/roles.restored.sql 2>/tmp/roles-diff.stderr; then
  echo "[restore-pair] FATAL: restored role inventory failed" >&2
  exit 1
fi
awk '$1 != "\\restrict" && $1 != "\\unrestrict" { print }' "${roles}" > /tmp/roles.source.canonical
awk '$1 != "\\restrict" && $1 != "\\unrestrict" { print }' /tmp/roles.restored.sql > /tmp/roles.restored.canonical
if [ "${RESTORE_SERVER_MODE:-standalone}" = cnpg ]; then
  python3 "${CNPG_ROLE_HELPER}" --source "${roles}" --current /tmp/roles.restored.sql
elif ! cmp -s /tmp/roles.source.canonical /tmp/roles.restored.canonical; then
  echo "[restore-pair] FATAL: role attributes, settings or memberships differ source_sha256=$(sha256sum /tmp/roles.source.canonical | awk '{print $1}') restored_sha256=$(sha256sum /tmp/roles.restored.canonical | awk '{print $1}')" >&2
  exit 1
fi
echo "[restore-pair] exact password-free role parity=true matview_refresh_rounds=${round}"
psql -X -qAt -v ON_ERROR_STOP=1 -v role_source=paired-password-free-backup \
  -d "${PGDATABASE}" -f "${AUDIT_SQL}"
if [ -n "${V2_INTERFACE_SQL:-}" ]; then
  if [ "${V2_INTERFACE_SQL}" != /scripts/qualify-v2-restored-interface.sql ] \
      && { [ "${RESTORE_SERVER_MODE:-standalone}" != cnpg ] \
           || [ "${V2_INTERFACE_SQL}" != /var/lib/postgresql/data/restore-custody/scripts/qualify-v2-restored-interface.sql ]; }; then
    echo "[restore-pair] FATAL: unrecognized v2 interface audit path" >&2
    exit 1
  fi
  first_interface="$(psql -X -qAt -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f "${V2_INTERFACE_SQL}")"
  second_interface="$(psql -X -qAt -v ON_ERROR_STOP=1 -d "${PGDATABASE}" -f "${V2_INTERFACE_SQL}")"
  if [ "${first_interface}" != "${second_interface}" ]; then
    echo "[restore-pair] FATAL: restored v2 interface audit changed across read-only replays" >&2
    exit 1
  fi
  echo "${first_interface}"
  echo "[restore-pair] PASS: restored v2 interface/role boundary (no assignment or setter call)"
fi
echo "[restore-pair] PASS: paired logical restore and bounded audit complete"
