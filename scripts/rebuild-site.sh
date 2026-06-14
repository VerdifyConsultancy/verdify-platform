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
# while Quartz works. A successful build is then rsynced into place with delayed
# deletes, avoiding the 404 window caused by Quartz clearing `public/`.

set -euo pipefail

LOCK=${VERDIFY_SITE_BUILD_LOCK:-/var/lock/verdify-site-build.lock}
LOG=${VERDIFY_SITE_BUILD_LOG:-/srv/verdify/state/site-build.log}
MARKER=${VERDIFY_SITE_BUILD_MARKER:-/var/local/verdify/state/site-build-last-run}
SITE_SOURCE=${VERDIFY_SITE_SOURCE:-/srv/verdify/site}
SITE_RUNTIME=${VERDIFY_SITE_RUNTIME:-/srv/verdify/verdify-site}
LIVE_PUBLIC=${VERDIFY_SITE_PUBLIC:-"$SITE_RUNTIME/public"}
BUILD_ROOT=${VERDIFY_SITE_BUILD_ROOT:-"$SITE_RUNTIME/.builds"}
RSYNC_IO_TIMEOUT=${VERDIFY_SITE_RSYNC_TIMEOUT:-180}
SITE_CONTAINER=${VERDIFY_SITE_CONTAINER:-verdify-site}
mkdir -p "$(dirname "$LOG")"
mkdir -p "$(dirname "$MARKER")"

{
    flock -n 9 || {
        echo "$(date -Is) build already running — skipping (changes will be picked up)"
        exit 0
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

    mkdir -p "$BUILD_ROOT" "$LIVE_PUBLIC"
    staging=""
    cleanup() {
        if [ -n "${staging:-}" ]; then
            rm -rf "$staging"
        fi
    }
    trap cleanup EXIT

    cd "$SITE_RUNTIME"
    build_ok=false
    for attempt in 1 2; do
        if [ -n "$staging" ]; then
            rm -rf "$staging"
        fi
        staging="$(mktemp -d "$BUILD_ROOT/public.XXXXXXXX")"
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

        rsync -a --delete-delay --timeout="$RSYNC_IO_TIMEOUT" "$staging"/ "$LIVE_PUBLIC"/

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
