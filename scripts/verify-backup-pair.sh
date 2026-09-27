#!/usr/bin/env bash
# Verify one committed dump/role pair before an isolated restore reads either.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: verify-backup-pair.sh BACKUP_DIR verdify-YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi
backup_dir="$1"
stem="$2"
if [[ ! "${stem}" =~ ^verdify-[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "[backup-pair] invalid backup identity" >&2
  exit 1
fi
for suffix in dump roles.sql sha256; do
  path="${backup_dir}/${stem}.${suffix}"
  if [ ! -f "${path}" ] || [ -L "${path}" ] || [ ! -s "${path}" ]; then
    echo "[backup-pair] missing, empty or linked artifact: ${stem}.${suffix}" >&2
    exit 1
  fi
done
manifest="${backup_dir}/${stem}.sha256"
mapfile -t lines < "${manifest}"
if [ "${#lines[@]}" -ne 3 ] \
   || [[ ! "${lines[0]}" =~ ^#[[:space:]]verdify-backup-pair-v1[[:space:]]database=([A-Za-z_][A-Za-z0-9_]*)[[:space:]]owner=([A-Za-z_][A-Za-z0-9_]*)$ ]]; then
  echo "[backup-pair] invalid manifest header" >&2
  exit 1
fi
database="${BASH_REMATCH[1]}"
owner="${BASH_REMATCH[2]}"
for suffix in dump roles.sql; do
  if [ "${suffix}" = dump ]; then line="${lines[1]}"; else line="${lines[2]}"; fi
  if [[ ! "${line:0:64}" =~ ^[0-9a-f]{64}$ ]] \
     || [ "${line:64}" != "  ${stem}.${suffix}" ]; then
    echo "[backup-pair] invalid checksum record" >&2
    exit 1
  fi
done
if ! (cd "${backup_dir}" && sha256sum -c -- "${stem}.sha256" >/dev/null 2>&1); then
  echo "[backup-pair] checksum mismatch" >&2
  exit 1
fi
if grep -Eq '^(CREATE|ALTER) ROLE .* PASSWORD ' "${backup_dir}/${stem}.roles.sql"; then
  echo "[backup-pair] role artifact contains a password clause" >&2
  exit 1
fi
if [ "$(grep -Fxc "CREATE ROLE ${owner};" "${backup_dir}/${stem}.roles.sql" || true)" -ne 1 ]; then
  echo "[backup-pair] database owner role is absent" >&2
  exit 1
fi
printf '%s|%s\n' "${database}" "${owner}"
