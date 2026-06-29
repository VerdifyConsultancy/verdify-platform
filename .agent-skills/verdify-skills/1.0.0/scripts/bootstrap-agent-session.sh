#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap-agent-session.sh codex|claude TARGET_REPOSITORY

Environment:
  VERDIFY_SKILLS_REF           Required immutable release tag or commit SHA.
  VERDIFY_SKILLS_REPOSITORY    Git URL (default: official Verdify repository).
  VERDIFY_SKILLS_CACHE_DIR     Cache directory.
  VERDIFY_ALLOW_MOVING_REF=1   Explicitly allow main/master/HEAD/latest (not recommended).
EOF
}

[[ $# -eq 2 ]] || { usage >&2; exit 2; }
host="$1"
target="$(cd "$2" && pwd)"
case "$host" in codex|claude) ;; *) echo "host must be codex or claude" >&2; exit 2;; esac

ref="${VERDIFY_SKILLS_REF:-}"
[[ -n "$ref" ]] || { echo "VERDIFY_SKILLS_REF is required; pin a release tag or commit" >&2; exit 2; }
case "$ref" in main|master|HEAD|latest|refs/heads/*)
  [[ "${VERDIFY_ALLOW_MOVING_REF:-0}" == "1" ]] || { echo "moving ref '$ref' rejected; pin a tag/commit or explicitly set VERDIFY_ALLOW_MOVING_REF=1" >&2; exit 2; }
  ;;
esac

repo="${VERDIFY_SKILLS_REPOSITORY:-https://github.com/VerdifyConsultancy/verdify-skills.git}"
cache="${VERDIFY_SKILLS_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/verdify-skills}"
key="$(ruby -rdigest -e 'print Digest::SHA256.hexdigest(ARGV.join("\0"))[0,24]' "$repo" "$ref")"
dest="$cache/$key"
mkdir -p "$cache"

if [[ ! -d "$dest/.git" ]]; then
  tmp="$dest.tmp.$$"
  rm -rf "$tmp"
  git init -q "$tmp"
  git -C "$tmp" remote add origin "$repo"
  git -C "$tmp" fetch -q --depth 1 origin "$ref"
  git -C "$tmp" checkout -q --detach FETCH_HEAD
  mv "$tmp" "$dest"
fi

resolved="$(git -C "$dest" rev-parse HEAD)"
if [[ "$ref" =~ ^[0-9a-fA-F]{40}$ ]] && [[ "${resolved,,}" != "${ref,,}" ]]; then
  echo "resolved commit $resolved does not match pinned commit $ref" >&2
  exit 1
fi

ruby "$dest/scripts/setup-agent-hosts.rb" --root "$target" --source "$dest" --host "$host"
printf 'Verdify skills %s installed for %s in %s\n' "$resolved" "$host" "$target"
printf 'Run: %s/bin/verdify init --repo %q\n' "$dest" "$target"
