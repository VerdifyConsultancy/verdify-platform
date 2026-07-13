#!/usr/bin/env bash
# rebuild-site.sh — Debounced Quartz rebuild + low-downtime publish.
# Invoked by verdify-site-build.service (triggered by verdify-site-build.path
# file watcher on /mnt/iris/verdify-vault/website/) and runnable manually:
#
#   make site-rebuild    (convenience target)
#   /srv/verdify/scripts/rebuild-site.sh
#
# Uses flock to serialize concurrent invocations. Builds happen outside the
# live `public/` directory so nginx keeps serving the previous complete site
# while Quartz works. A successful candidate is then atomically exchanged with
# the live directory, avoiding partial trees and the Quartz clear-and-rebuild
# 404 window.

set -euo pipefail

LOCK=${VERDIFY_SITE_BUILD_LOCK:-/var/lock/verdify-site-build.lock}
LOG=${VERDIFY_SITE_BUILD_LOG:-/srv/verdify/state/site-build.log}
MARKER=${VERDIFY_SITE_BUILD_MARKER:-/var/local/verdify/state/site-build-last-run}
SITE_SOURCE=${VERDIFY_SITE_SOURCE:-/srv/verdify/site}
SITE_RUNTIME=${VERDIFY_SITE_RUNTIME:-/srv/verdify/verdify-site}
LIVE_PUBLIC=${VERDIFY_SITE_PUBLIC:-"$SITE_RUNTIME/public"}
BUILD_ROOT=${VERDIFY_SITE_BUILD_ROOT:-"$SITE_RUNTIME/.builds"}
SITE_CONTAINER=${VERDIFY_SITE_CONTAINER:-verdify-site}
LOCKED_RC=${VERDIFY_SITE_BUILD_LOCKED_RC:-0}
SCRIPT_ROOT=${VERDIFY_SCRIPT_ROOT:-/srv/verdify/scripts}
PYTHON=${PYTHON:-python3}
PUBLIC_OUTPUT_GUARD=${VERDIFY_PUBLIC_OUTPUT_GUARD:-"$SCRIPT_ROOT/check-public-output.py"}
PUBLIC_OUTPUT_REPORT=${VERDIFY_PUBLIC_OUTPUT_BUILD_REPORT:-/srv/verdify/state/public-output-build-report.json}
ATOMIC_PROMOTER=${VERDIFY_ATOMIC_DIRECTORY_PROMOTER:-"$SCRIPT_ROOT/atomic-promote-directory.py"}
# The bounded guard timeout is the publish backpressure guard. Its default
# keeps real headroom over the measured worst case (125.06s for the 1,184-file
# stage tree in docs/reviews/public-output-guard-performance-2026-07-11.md):
# the timeout stays fail-closed, but it must not SIGKILL a legitimate
# full-tree scan as the public tree grows.
PUBLIC_OUTPUT_GUARD_TIMEOUT=${VERDIFY_PUBLIC_OUTPUT_GUARD_TIMEOUT:-300}
STALE_CANDIDATE_MIN_AGE=${VERDIFY_STALE_CANDIDATE_MIN_AGE:-3600}
if ! [[ "$PUBLIC_OUTPUT_GUARD_TIMEOUT" =~ ^[0-9]+$ ]] \
    || ((PUBLIC_OUTPUT_GUARD_TIMEOUT < 30 || PUBLIC_OUTPUT_GUARD_TIMEOUT > 600)); then
    echo "public-output guard timeout must be between 30 and 600 seconds" >&2
    exit 2
fi
if ! [[ "$STALE_CANDIDATE_MIN_AGE" =~ ^[0-9]+$ ]] \
    || ((STALE_CANDIDATE_MIN_AGE < 300 || STALE_CANDIDATE_MIN_AGE > 86400)); then
    echo "stale candidate age must be between 300 and 86400 seconds" >&2
    exit 2
fi
mkdir -p "$(dirname "$LOG")"
mkdir -p "$(dirname "$MARKER")"

{
    flock -n 9 || {
        echo "$(date -Is) build already running — skipping (changes will be picked up)"
        exit "$LOCKED_RC"
    }

    # Small debounce so multi-file Syncthing drops coalesce into one build
    sleep 5

    echo "$(date -Is) rebuild starting"
    nginx_changed=false
    if [ -d "$SITE_SOURCE/quartz" ]; then
        if [ -f "$SITE_SOURCE/nginx.conf" ] && ! cmp -s "$SITE_SOURCE/nginx.conf" "$SITE_RUNTIME/nginx.conf"; then
            nginx_changed=true
        fi
        rsync -a --delete --exclude '.quartz-cache' "$SITE_SOURCE/quartz/" "$SITE_RUNTIME/quartz/"
        rsync -a --delete "$SITE_SOURCE/docs/" "$SITE_RUNTIME/docs/"
        rsync -a \
            "$SITE_SOURCE/package.json" \
            "$SITE_SOURCE/package-lock.json" \
            "$SITE_SOURCE/quartz.config.ts" \
            "$SITE_SOURCE/quartz.layout.ts" \
            "$SITE_SOURCE/tsconfig.json" \
            "$SITE_SOURCE/globals.d.ts" \
            "$SITE_SOURCE/index.d.ts" \
            "$SITE_SOURCE/nginx.conf" \
            "$SITE_RUNTIME/"
    fi

    live_parent="$(dirname "$LIVE_PUBLIC")"
    live_name="$(basename "$LIVE_PUBLIC")"
    mkdir -p "$BUILD_ROOT" "$live_parent"
    if ! "$PYTHON" "$ATOMIC_PROMOTER" \
        --cleanup-stale "$LIVE_PUBLIC" \
        --min-age-seconds "$STALE_CANDIDATE_MIN_AGE"; then
        echo "$(date -Is) stale candidate recovery FAILED"
        exit 1
    fi
    staging=""
    discard_staging() {
        if [ -n "${staging:-}" ]; then
            "$PYTHON" "$ATOMIC_PROMOTER" --discard-candidate "$staging" >/dev/null 2>&1 || true
        fi
    }
    cleanup() {
        discard_staging
    }
    trap cleanup EXIT

    cd "$SITE_RUNTIME"
    build_ok=false
    for attempt in 1 2; do
        if [ -n "$staging" ]; then
            discard_staging
        fi
        staging="$(mktemp -d "$live_parent/.${live_name}.candidate.XXXXXXXX")"
        if npx quartz build --output "$staging" 2>&1 | tail -5; then
            if [ -f "$staging/index.html" ]; then
                build_ok=true
                break
            fi
            echo "$(date -Is) quartz build attempt $attempt FAILED — staging index.html missing"
        else
            echo "$(date -Is) quartz build attempt $attempt FAILED"
        fi
        if [ "$attempt" -lt 2 ]; then
            echo "$(date -Is) retrying quartz build after transient failure"
            sleep 5
        fi
    done

    if [ "$build_ok" = true ]; then

        # Quartz builds directly into a private same-filesystem candidate. The
        # guard keeps that exact directory descriptor and its parent open from
        # scan through descriptor-relative promotion; there is no post-scan
        # copy or separate pathname-based promoter handoff.
        if ! timeout --kill-after=10s "${PUBLIC_OUTPUT_GUARD_TIMEOUT}s" \
            "$PYTHON" "$PUBLIC_OUTPUT_GUARD" \
                --root "$staging" \
                --json-report "$PUBLIC_OUTPUT_REPORT" \
                --promote-to "$LIVE_PUBLIC"; then
            echo "$(date -Is) public-output guard FAILED — live public tree unchanged"
            # The guard descriptor-cleans only the inode it scanned. A timeout
            # or rejected pathname swap is left for age/identity-gated recovery;
            # the shell must never rm a possibly substituted pathname.
            staging=""
            exit 1
        fi
        # Normal promotion descriptor-cleans the retired live tree. A SIGKILL
        # between exchange and cleanup intentionally leaves the old tree under
        # this candidate name for age/identity-gated startup recovery.
        staging=""

        if [ "$nginx_changed" = true ] && [ -n "$SITE_CONTAINER" ]; then
            if docker exec "$SITE_CONTAINER" nginx -s reload > /dev/null 2>&1; then
                nginx_action="nginx reloaded"
            elif docker restart "$SITE_CONTAINER" > /dev/null 2>&1; then
                nginx_action="nginx restarted after reload failure"
            else
                echo "$(date -Is) quartz built but nginx reload/restart failed"
                exit 1
            fi
        elif [ "$nginx_changed" = true ]; then
            nginx_action="nginx reload skipped (no VERDIFY_SITE_CONTAINER)"
        else
            nginx_action="nginx left running"
        fi

        pages=$(find "$LIVE_PUBLIC" -name '*.html' | wc -l)
        touch "$MARKER"
        echo "$(date -Is) rebuild complete — $pages pages emitted, $nginx_action"
        find "$BUILD_ROOT" -maxdepth 1 -type d -name 'public.*' -mtime +1 -exec rm -rf {} + 2>/dev/null || true
    else
        echo "$(date -Is) quartz build FAILED"
        exit 1
    fi
} 9>"$LOCK" 2>&1 | tee -a "$LOG"
