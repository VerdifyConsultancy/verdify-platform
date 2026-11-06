# shellcheck shell=bash
# Shared helpers for the GitHub Actions jobs in .github/workflows (issue #1397).
# Sourced, never executed. Works in the ARC runner container and in a job-image
# sidecar: needs only bash and coreutils (git and python3 for the helpers).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

# A writable private HOME for uv, pip, pnpm and PlatformIO caches. The job-image
# sidecars run as UID 1001 and their image HOME may be read-only or root-owned.
if [ -z "${HOME:-}" ] || [ ! -w "${HOME}" ]; then
  HOME="${TMPDIR:-/tmp}/verdify-ci-home"
  mkdir -p "$HOME"
  export HOME
fi
export CI=true

# Make the history needed for a base revision available. actions/checkout makes
# a depth-1 clone; a blobless unshallow fetches commits and trees only, and git
# fetches any blob a diff gate reads on demand from the public repository.
ci_fetch_history() {
  if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
    git fetch --quiet --no-tags --filter=blob:none --unshallow origin \
      "+refs/heads/main:refs/remotes/origin/main"
  else
    git fetch --quiet --no-tags --filter=blob:none origin \
      "+refs/heads/main:refs/remotes/origin/main"
  fi
}

# The base revision the native pipeline diffs against:
#   pull_request: the PR's base on main (the checkout is GitHub's merge commit,
#                 so its merge base with origin/main is the main it merges into);
#   push to main: the previous main head (first parent);
#   dispatch (pin branches): the merge base with origin/main.
# Prints nothing when no usable base exists; the classifier then falls back to
# "everything impacted", as the native pipeline does.
ci_base() {
  local head base
  head="$(git rev-parse HEAD)"
  case "${GITHUB_EVENT_NAME:-}" in
    push) base="$(git rev-parse --verify --quiet "HEAD^1" || true)" ;;
    *) base="$(git merge-base origin/main HEAD 2>/dev/null || true)"
       if [ "$base" = "$head" ]; then
         base="$(git rev-parse --verify --quiet "HEAD^1" || true)"
       fi ;;
  esac
  printf '%s' "$base"
}

# Run the vendored path-impact classifier into $1. Extra args go to it (--push).
ci_classify() {
  local out="$1"; shift
  python3 ci/gha/verdify_path_impact.py --root . --head "$(git rev-parse HEAD)" \
    --base "$(ci_base)" --output "$out" "$@"
}

# TEMPORARY until estate-ci toolbox image adds kustomize + UID 1001 passwd entry (CI-PLATFORM):
# ci_ensure_kustomize and ci_ensure_passwd_entry below; delete both then.
#
# kustomize v5.4.3, the release the native step downloads, checked against the
# release's published SHA-256 (the value ci/validate.Dockerfile also pins).
# Installed into the private HOME when the job image has none (python and
# agents-ci-tools job images); tests call `kustomize build` directly.
ci_ensure_kustomize() {
  command -v kustomize >/dev/null && return 0
  local bin="$HOME/.local/bin"
  mkdir -p "$bin"
  curl -fsSL --retry 3 -o "$bin/kustomize.tgz" \
    "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz"
  echo "3669470b454d865c8184d6bce78df05e977c9aea31c30df3c669317d43bcc7a7  $bin/kustomize.tgz" | sha256sum -c -
  tar -xzf "$bin/kustomize.tgz" --no-same-owner -C "$bin" kustomize
  rm "$bin/kustomize.tgz"
  export PATH="$bin:$PATH"
}

# Give the job UID a passwd/group entry when the image has none: the job-image
# sidecars run as UID 1001, which python:3.13-bookworm does not define, and
# initdb (the private PostgreSQL fixtures) refuses a UID without one. Same
# nss_wrapper mechanism as scripts/restore-backup-pair.sh; the library comes
# from the image's own signed Debian sources, downloaded and extracted into
# private state without installing anything.
ci_ensure_passwd_entry() {
  getent passwd "$(id -u)" >/dev/null && return 0
  local root="$HOME/nss-wrapper" lib
  mkdir -p "$root/lists/partial" "$root/cache/archives/partial" "$root/debs"
  local apt=(apt-get -qq -o "Dir::State::lists=$root/lists" -o "Dir::Cache=$root/cache"
    -o Debug::NoLocking=true -o Acquire::AllowInsecureRepositories=false -o Acquire::Retries=5)
  "${apt[@]}" update
  (cd "$root/debs" && "${apt[@]}" download libnss-wrapper)
  dpkg-deb -x "$(ls "$root"/debs/libnss-wrapper_*.deb)" "$root/root"
  lib="$(find "$root/root" -name libnss_wrapper.so -print -quit)"
  [ -n "$lib" ] || { echo "libnss_wrapper.so not found in libnss-wrapper" >&2; return 1; }
  { cat /etc/passwd; echo "ci:x:$(id -u):$(id -g):ci:$HOME:/bin/bash"; } > "$root/passwd"
  { cat /etc/group; getent group "$(id -g)" >/dev/null || echo "ci:x:$(id -g):"; } > "$root/group"
  export LD_PRELOAD="$lib${LD_PRELOAD:+:$LD_PRELOAD}"
  export NSS_WRAPPER_PASSWD="$root/passwd" NSS_WRAPPER_GROUP="$root/group"
  export USER=ci LOGNAME=ci
  # The preload puts nss_wrapper ahead of the ASan runtime in the sanitizer
  # tests' binaries (tests/test_api_overflow_patch.py); ASan then refuses to
  # start. Skip only that link-order check: nss_wrapper wraps no allocator, so
  # ASan/UBSan detection is unchanged.
  export ASAN_OPTIONS="verify_asan_link_order=0${ASAN_OPTIONS:+:$ASAN_OPTIONS}"
  getent passwd "$(id -u)" >/dev/null || { echo "nss_wrapper did not take effect" >&2; return 1; }
}
