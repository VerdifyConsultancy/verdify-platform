#!/usr/bin/env bash
# psql-verdify.sh — single entrypoint for Verdify DB access from shell (#24).
#
# A1 FREEZE-CRITICAL. The VM today reaches the database via
#   docker exec verdify-timescaledb psql -U verdify -d verdify ...
# scattered across a dozen scripts, the Makefile, the firmware-deploy preflight,
# the replay exporters, and sensor-health. The k3s migration needs those same
# scripts to reach an in-cluster Postgres (no `docker exec`, no local socket).
# This library abstracts the CONNECTION PREFIX behind one function so the call
# sites pass only psql flags/SQL and stay mode-agnostic.
#
# DESIGN GUARANTEE: the DEFAULT mode is `docker-exec` with the exact same
# container/user/db the call sites used, so behavior on the live VM is byte
# identical. Nothing here changes what SQL runs or how output is shaped — it
# only chooses HOW psql is invoked.
#
# Usage (source it, then call verdify_psql with the SAME flags you'd pass psql):
#
#   . "$(dirname "$0")/lib/psql-verdify.sh"           # adjust relative path
#   verdify_psql -t -A -F '|' -c "SELECT 1"            # one-shot query
#   echo "SELECT 1" | verdify_psql -t -A -f -          # stdin
#   verdify_psql -c "COPY (...) TO STDOUT ..." > out   # COPY to stdout
#
# To capture the command as an array (for call sites that keep a DB=(...) array):
#   mapfile -t DB < <(verdify_psql_cmd)                # NOT needed; see helpers
#   "${DB[@]}" -t -A -c "SELECT 1"
#
# Modes (VERDIFY_PSQL_MODE):
#   docker-exec  (default) — docker exec [-i] <container> psql -U <user> -d <db>
#   direct                 — psql against PG* / VERDIFY_DB_* env (in-cluster /
#                            host with a reachable socket or TCP endpoint)
#   in-cluster             — alias of direct; reads DB_HOST/DB_PORT/DB_NAME/
#                            DB_USER/POSTGRES_PASSWORD (the k3s ConfigMap+Secret
#                            contract) and exports PG* for psql.
#
# Env knobs (all optional; defaults preserve VM behavior):
#   VERDIFY_PSQL_MODE       docker-exec | direct | in-cluster   (default docker-exec)
#   VERDIFY_DB_CONTAINER    docker container name   (default verdify-timescaledb)
#   VERDIFY_DB_USER         db user                 (default verdify)
#   VERDIFY_DB_NAME         db name                 (default verdify)
#   VERDIFY_DOCKER_STDIN    1 to add `docker exec -i` (for piped/heredoc SQL)
#
# The function intentionally does NOT bake -t/-A/-F/-c etc.: callers pass those
# so each site keeps its exact formatting flags.

# Guard against double-sourcing.
if [[ -n "${_VERDIFY_PSQL_LIB_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_VERDIFY_PSQL_LIB_LOADED=1

_VERDIFY_PSQL_MODE_DEFAULT="docker-exec"
VERDIFY_PSQL_MODE="${VERDIFY_PSQL_MODE:-$_VERDIFY_PSQL_MODE_DEFAULT}"
VERDIFY_DB_CONTAINER="${VERDIFY_DB_CONTAINER:-verdify-timescaledb}"
VERDIFY_DB_USER="${VERDIFY_DB_USER:-${DB_USER:-verdify}}"
VERDIFY_DB_NAME="${VERDIFY_DB_NAME:-${DB_NAME:-verdify}}"

# verdify_psql_cmd — print the connection-prefix argv (one token per line) for
# the active mode. Call sites that keep a `DB=(...)` array do:
#     mapfile -t DB < <(verdify_psql_cmd); "${DB[@]}" -t -A -c "SELECT 1"
# Pass extra `docker exec` flags (e.g. -e PGOPTIONS=...) as args; they are
# inserted right after `docker exec` in docker-exec mode and ignored otherwise.
verdify_psql_cmd() {
    local -a docker_extra=("$@")
    case "$VERDIFY_PSQL_MODE" in
        docker-exec)
            printf '%s\n' docker exec
            if [[ "${VERDIFY_DOCKER_STDIN:-0}" == "1" ]]; then
                printf '%s\n' -i
            fi
            local f
            for f in "${docker_extra[@]}"; do
                printf '%s\n' "$f"
            done
            printf '%s\n' "$VERDIFY_DB_CONTAINER" psql -U "$VERDIFY_DB_USER" -d "$VERDIFY_DB_NAME"
            ;;
        direct|in-cluster)
            # Resolve connection from the k3s ConfigMap/Secret contract, falling
            # back to PG* if already set. psql reads PG* env, so export them and
            # emit a bare `psql`.
            export PGHOST="${PGHOST:-${DB_HOST:-localhost}}"
            export PGPORT="${PGPORT:-${DB_PORT:-5432}}"
            export PGDATABASE="${PGDATABASE:-$VERDIFY_DB_NAME}"
            export PGUSER="${PGUSER:-$VERDIFY_DB_USER}"
            if [[ -z "${PGPASSWORD:-}" ]]; then
                if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
                    export PGPASSWORD="$POSTGRES_PASSWORD"
                elif [[ -n "${DB_PASS:-}" ]]; then
                    export PGPASSWORD="$DB_PASS"
                fi
            fi
            printf '%s\n' psql
            ;;
        *)
            echo "psql-verdify.sh: unknown VERDIFY_PSQL_MODE='$VERDIFY_PSQL_MODE'" >&2
            return 2
            ;;
    esac
}

# verdify_psql — run psql against the Verdify DB in the active mode. All args are
# passed straight to psql (after the connection prefix). stdin is forwarded, so
# heredoc/piped SQL works in docker-exec mode IF VERDIFY_DOCKER_STDIN=1 (or call
# verdify_psql_stdin which sets it for you).
verdify_psql() {
    local -a cmd
    mapfile -t cmd < <(verdify_psql_cmd) || return $?
    "${cmd[@]}" "$@"
}

# verdify_psql_stdin — like verdify_psql but guarantees the container gets stdin
# (docker exec -i). Use for `... | verdify_psql_stdin -f -` or heredocs.
verdify_psql_stdin() {
    VERDIFY_DOCKER_STDIN=1 verdify_psql "$@"
}

# verdify_psql_cmd_with — print the connection-prefix argv but with extra
# docker-exec flags (e.g. -e PGOPTIONS). Thin wrapper for readability.
verdify_psql_cmd_with() {
    verdify_psql_cmd "$@"
}
