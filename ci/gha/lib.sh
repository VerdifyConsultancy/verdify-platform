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

# The estate-ci runner image (estate-ci#39) provides kustomize on PATH and a
# passwd/group entry for the job UID; fail fast if either is missing rather
# than skip the gates that need them (kustomize build, initdb).
ci_require_runner_tools() {
  command -v kustomize >/dev/null || { echo "runner image is missing kustomize (estate-ci#39)" >&2; return 1; }
  getent passwd "$(id -u)" >/dev/null || { echo "runner image has no passwd entry for UID $(id -u) (estate-ci#39)" >&2; return 1; }
}
