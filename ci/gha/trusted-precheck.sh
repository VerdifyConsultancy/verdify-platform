#!/usr/bin/env bash
# GHA trusted-precheck: the native pipeline's webhook-envelope check, rebuilt on
# the runner's GITHUB_* environment. Validates the event, repository, refs and
# the exact checked-out revision before any repository gate runs.
#
# The estate-ci job-started guard (a ConfigMap the PR cannot change) already
# refuses fork PRs, other repositories, pull_request_target and workflow_run on
# the runner; this script is the repository-side half of the same contract.
set -eu
fail() {
  echo "Verdify Platform validation precheck rejected the event or revision: $*" >&2
  exit 1
}
is_sha() {
  case "$1" in *[!0-9a-f]*|"") return 1 ;; esac
  [ "${#1}" -eq 40 ]
}
safe_ref() {
  case "$1" in *[!A-Za-z0-9._/-]*|""|/*|*/|*..*) return 1 ;; esac
}

# Accept both owners while the repository moves to jvallery (D19); the name is fixed.
case "${GITHUB_REPOSITORY:-}" in
  jvallery/verdify-platform|VerdifyConsultancy/verdify-platform) ;;
  *) fail "repository ${GITHUB_REPOSITORY:-unset}" ;;
esac
case "${GITHUB_EVENT_NAME:-}" in
  pull_request)
    [ "${GITHUB_BASE_REF:-}" = main ] || fail "base ref ${GITHUB_BASE_REF:-unset}"
    safe_ref "${GITHUB_HEAD_REF:-}" || fail "head ref"
    ;;
  push)
    [ "${GITHUB_REF:-}" = refs/heads/main ] || fail "push ref ${GITHUB_REF:-unset}"
    ;;
  workflow_dispatch)
    # estate-ci pin-bump dispatches ci.yml on its pin branch.
    case "${GITHUB_REF:-}" in refs/heads/*) ;; *) fail "dispatch ref" ;; esac
    safe_ref "${GITHUB_REF#refs/heads/}" || fail "dispatch ref"
    ;;
  *) fail "event ${GITHUB_EVENT_NAME:-unset}" ;;
esac
is_sha "${GITHUB_SHA:-}" || fail "GITHUB_SHA"
head="$(git rev-parse HEAD)"
is_sha "$head" || fail "checkout HEAD"
[ "$head" = "$GITHUB_SHA" ] || fail "checkout ${head} differs from GITHUB_SHA"
echo "precheck: ${GITHUB_EVENT_NAME} ${GITHUB_REF:-} ${head} full validation"
