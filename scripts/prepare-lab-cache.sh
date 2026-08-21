#!/usr/bin/env bash
# Initialize the shared Lab cache without exposing a partially copied tree.
#
# Both the publisher CronJob and the Lab Deployment run this exact helper from
# the publisher image.  The persistent wrapper lock serializes migration,
# optional bootstrap seeding, and the publisher's later scan/promote phase.
set -euo pipefail

ORIGINAL_ARGS=("$@")
ROOT="/work/publisher"
LEGACY="/work/public"
BOOTSTRAP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$2"
      shift 2
      ;;
    --legacy)
      LEGACY="$2"
      shift 2
      ;;
    --bootstrap)
      BOOTSTRAP="$2"
      shift 2
      ;;
    *)
      echo "unsupported Lab cache initialization argument" >&2
      exit 2
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLIC_OUTPUT_GUARD="${VERDIFY_PUBLIC_OUTPUT_GUARD:-/usr/local/bin/check-public-output}"
if [[ ! -f "$PUBLIC_OUTPUT_GUARD" && -f "$SCRIPT_DIR/check-public-output.py" ]]; then
  PUBLIC_OUTPUT_GUARD="$SCRIPT_DIR/check-public-output.py"
fi
PUBLIC_OUTPUT_PYTHON="${VERDIFY_PUBLIC_OUTPUT_PYTHON:-${PYTHON:-python3}}"
CACHE_PYTHON="${VERDIFY_CACHE_PYTHON:-python3}"
CACHE_LOCK_HELPER="${VERDIFY_CACHE_LOCK_HELPER:-/usr/local/bin/prepare-lab-cache-lock}"
if [[ ! -f "$CACHE_LOCK_HELPER" && -f "$SCRIPT_DIR/prepare-lab-cache-lock.py" ]]; then
  CACHE_LOCK_HELPER="$SCRIPT_DIR/prepare-lab-cache-lock.py"
fi
PUBLIC_OUTPUT_GUARD_TIMEOUT="${VERDIFY_PUBLIC_OUTPUT_GUARD_TIMEOUT:-300}"
if ! [[ "$PUBLIC_OUTPUT_GUARD_TIMEOUT" =~ ^[0-9]+$ ]] \
    || ((PUBLIC_OUTPUT_GUARD_TIMEOUT < 30 || PUBLIC_OUTPUT_GUARD_TIMEOUT > 600)); then
  echo "Lab cache public validation configuration failed" >&2
  exit 2
fi

umask 077

READY="$ROOT/.layout-v2-scanned-ready"
PUBLIC="$ROOT/public"
OLD="$ROOT/.layout-v1-old"

if [[ ! -f "$CACHE_LOCK_HELPER" ]]; then
  echo "Lab cache lock initialization failed" >&2
  exit 2
fi
if [[ "${VERDIFY_CACHE_LOCK_HELD_FD:-}" != "9" ]]; then
  exec "$CACHE_PYTHON" "$CACHE_LOCK_HELPER" \
    --root "$ROOT" \
    --fd 9 \
    -- \
    bash "$0" "${ORIGINAL_ARGS[@]}"
fi
if ! "$CACHE_PYTHON" "$CACHE_LOCK_HELPER" --root "$ROOT" --fd 9 --verify-held >/dev/null 2>&1; then
  echo "Lab cache lock initialization failed" >&2
  exit 2
fi

validate_public_tree() {
  local source="$1"
  local diagnostic="${2:-Lab cache public tree validation failed}"
  local attestation="${3:-}"
  local -a guard_args=(--root "$source")

  if [[ -n "$attestation" ]]; then
    guard_args+=(--attestation-report "$attestation")
  fi

  if [[ ! -f "$PUBLIC_OUTPUT_GUARD" ]] \
      || ! timeout --kill-after=5s "${PUBLIC_OUTPUT_GUARD_TIMEOUT}s" \
        "$PUBLIC_OUTPUT_PYTHON" "$PUBLIC_OUTPUT_GUARD" "${guard_args[@]}" >/dev/null 2>&1; then
    if [[ -n "$attestation" ]]; then
      rm -f -- "$attestation"
    fi
    echo "$diagnostic" >&2
    return 1
  fi
  if [[ -n "$attestation" ]] && ! "$CACHE_PYTHON" -c '
import json
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_text(encoding="utf-8")
value = json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
expected = {"contract", "root_identity", "schema_version", "tree_digest"}
if not isinstance(value, dict) or set(value) != expected:
    raise ValueError
if value["contract"] != "verdify.public-output-layout-attestation" or value["schema_version"] != 1:
    raise ValueError
if not all(re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) for key in ("root_identity", "tree_digest")):
    raise ValueError
canonical = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
if raw != canonical:
    raise ValueError
' "$attestation" >/dev/null 2>&1; then
    rm -f -- "$attestation"
    echo "$diagnostic" >&2
    return 1
  fi
}

path_exists_nofollow() {
  [[ -L "$1" || -e "$1" ]]
}

require_real_directory() {
  local path="$1"
  local diagnostic="$2"

  if [[ -L "$path" || ! -d "$path" ]]; then
    echo "$diagnostic" >&2
    return 1
  fi
}

chmod_public_directory() {
  if ! "$CACHE_PYTHON" -c '
import os
import stat
import sys

flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
descriptor = os.open(sys.argv[1], flags)
try:
    if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o755:
        os.fchmod(descriptor, 0o755)
finally:
    os.close(descriptor)
' "$PUBLIC" >/dev/null 2>&1; then
    echo "Lab cache public root is not a directory" >&2
    return 1
  fi
}

# Validate every supplied source before recovery or any public-tree mutation.
# The scanner owns symlink/hardlink/special-entry and content policy; diagnostics
# stay fixed and non-reflective so an unsafe filename or value is never logged.
if [[ -L "$LEGACY" || -e "$LEGACY" ]]; then
  validate_public_tree "$LEGACY"
fi
if [[ -n "$BOOTSTRAP" ]]; then
  validate_public_tree "$BOOTSTRAP"
fi

# Classify both rename endpoints without following links.  If both names exist,
# validate the still-served live tree before touching the old residue.  Every
# old residue is independently validated before it can be renamed or removed.
public_present=false
old_present=false
if path_exists_nofollow "$PUBLIC"; then
  require_real_directory "$PUBLIC" "Lab cache public root is not a directory"
  public_present=true
fi
if path_exists_nofollow "$OLD"; then
  require_real_directory "$OLD" "Lab cache recovery residue validation failed"
  old_present=true
fi
if [[ "$public_present" == true && "$old_present" == true ]]; then
  validate_public_tree "$PUBLIC"
fi
if [[ "$old_present" == true ]]; then
  validate_public_tree "$OLD" "Lab cache recovery residue validation failed"
fi

# Recover either side of an interrupted two-rename replacement only after the
# exact old tree has passed the canonical scanner.  A new pod does not start
# until init completes; nginx resolves publisher/public by pathname.
if [[ "$public_present" == false && "$old_present" == true ]]; then
  mv -- "$OLD" "$PUBLIC"
  public_present=true
elif [[ "$public_present" == true && "$old_present" == true ]]; then
  rm -rf -- "$OLD"
fi
find "$ROOT" -mindepth 1 -maxdepth 1 -type d \
  \( -name '.layout-v1-init.*' -o -name '.layout-v2-init.*' \) \
  -user "$(id -u)" -exec rm -rf -- {} +

tree_has_entries() {
  [[ ! -L "$1" && -d "$1" ]] && [[ -n "$(find "$1" -mindepth 1 -print -quit)" ]]
}

has_regular_homepage() {
  [[ ! -L "$PUBLIC/index.html" && -f "$PUBLIC/index.html" ]]
}

# The baked fallback is only a fallback if it is actually THIS site.  The
# verdify-lab image is built in verdify-site-legacy, and when its Quartz build
# runs without the Verdify content tree it emits Quartz's own upstream docs
# ("Welcome to Quartz 4", philosophy/, migrating-from-Quartz-3/...).  The
# content-policy scanner passes that tree happily — nothing in it is
# prohibited, it is simply the wrong site — so on 2026-07-26 an emptied cache
# PVC seeded it and lab.verdify.ai served Quartz's documentation under the
# Verdify brand.
#
# Neither directory names nor homepage metadata discriminate.  `advanced/`,
# `features/`, `plugins/`, `images/`, `static/` and `tags/` exist in BOTH trees,
# and quartz.config.ts stamps og:site_name "Verdify Lab" plus the Verdify
# description onto a contentless build too.  Only content-derived routes do.
#
# INTERIM CHECK.  The canonical signal should be a build-owned identity marker
# emitted by verdify-site-legacy; that repo's sibling PR adds it, and this check
# tightens to require it once the corrected image is built and pinned.  Until
# then the route evidence below is the discriminator.
LAB_IDENTITY_ROUTES=(
  "plans/index.html"
  "data/forecast/index.html"
  "start/index.html"
  "greenhouse/index.html"
)

is_lab_site_tree() {
  local source="$1"
  local route

  [[ ! -L "$source/index.html" && -f "$source/index.html" ]] || return 1

  # Require the whole route set, not one marker: a single dummy plan page
  # dropped into a stock Quartz tree must not be able to satisfy this.
  for route in "${LAB_IDENTITY_ROUTES[@]}"; do
    [[ ! -L "$source/$route" && -f "$source/$route" ]] || return 1
  done

  # Strict YYYY-MM-DD.html.  A `????-??-??` glob also matches e.g.
  # `plan-x-y.html`, so spell the digits out.
  [[ ! -L "$source/plans" && -d "$source/plans" ]] || return 1
  [[ -n "$(find "$source/plans" -maxdepth 1 -type f \
    -name '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].html' -print -quit)" ]]
}

replace_public_from() {
  local source="$1"
  local candidate

  candidate="$(mktemp -d "$ROOT/.layout-v2-init.XXXXXX")"
  if ! cp -a -- "$source"/. "$candidate"/ >/dev/null 2>&1; then
    rm -rf -- "$candidate"
    echo "Lab cache public candidate copy failed" >&2
    return 1
  fi
  chmod 0755 -- "$candidate"
  if ! validate_public_tree "$candidate"; then
    rm -rf -- "$candidate"
    return 1
  fi
  # Identity is checked on the exact tree about to be installed, never only on
  # the pre-copy source: the candidate is what becomes the public site, and a
  # source-only check leaves a window where the two differ.  This covers the
  # legacy promotion path too — on 2026-07-26 the legacy directory was itself
  # the stock Quartz tree, so promoting it unchecked would have republished it.
  if ! is_lab_site_tree "$candidate"; then
    rm -rf -- "$candidate"
    echo "Lab cache candidate is not a Verdify Lab build; refusing to install it" >&2
    return 1
  fi

  if path_exists_nofollow "$OLD"; then
    echo "Lab cache recovery residue validation failed" >&2
    return 1
  fi
  if path_exists_nofollow "$PUBLIC"; then
    require_real_directory "$PUBLIC" "Lab cache public root is not a directory"
    mv -- "$PUBLIC" "$OLD"
  fi
  mv -- "$candidate" "$PUBLIC"
  rm -rf -- "$OLD"
}

# The marker records layout state only.  It is never consulted as content trust:
# every invocation rescans the current public tree and atomically refreshes the
# marker with the scanner's root identity plus canonical inventory digest.
if ! tree_has_entries "$PUBLIC" && tree_has_entries "$LEGACY"; then
  replace_public_from "$LEGACY"
elif ! path_exists_nofollow "$PUBLIC"; then
  mkdir -- "$PUBLIC"
fi
require_real_directory "$PUBLIC" "Lab cache public root is not a directory"
validate_public_tree "$PUBLIC"
chmod_public_directory

# Only the Lab Deployment supplies a baked bootstrap.  It is installed while
# holding the same lock used by the publisher main process, and only when the
# completed live tree has no homepage.  Thus pod order is irrelevant.
# Refuse a foreign tree rather than publish it.  replace_public_from validates
# the copied candidate and returns non-zero, so `set -e` fails init here: that
# keeps an already-serving pod in place (maxUnavailable: 0) and makes an
# unusable bootstrap a loud, alertable rollout failure instead of a silently
# wrong public site.
if [[ -n "$BOOTSTRAP" ]] && ! has_regular_homepage && tree_has_entries "$BOOTSTRAP"; then
  replace_public_from "$BOOTSTRAP"
fi

# Validate the exact current pathname before success.  Any permission change
# already followed a clean scan and used an O_NOFOLLOW directory descriptor;
# this final pass binds the marker to the final served state.
require_real_directory "$PUBLIC" "Lab cache public root is not a directory"
ready_tmp="$(mktemp "$ROOT/.layout-v2-scanned-ready.XXXXXX")"
validate_public_tree "$PUBLIC" "Lab cache public tree validation failed" "$ready_tmp"

# Do not certify an empty publisher-only cache: the later site initializer must
# validate its baked bootstrap (or a publisher-generated homepage) first.
if has_regular_homepage; then
  chmod 0600 -- "$ready_tmp"
  mv -f -- "$ready_tmp" "$READY"
else
  rm -f -- "$ready_tmp" "$READY"
fi
