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
# Backend switch (VERDIFY_DB_BACKEND) — the issue-#24/#254 contract:
#   docker  (DEFAULT) — docker exec [-i] <container> psql ...  (byte-identical VM)
#   dsn               — bare psql against PG* / k3s ConfigMap+Secret env (no
#                       docker socket; in-cluster or any host with a reachable
#                       Postgres endpoint).
#   kube              — <kubectl> exec [-i] -n <ns> <pod> -c <ctr> -- psql ...
#                       (#254: re-homes the VM `docker exec verdify-timescaledb`
#                       firmware preflight DB handle to the k3s prod DB from a
#                       tooling host that has kubectl but no Postgres socket —
#                       the headless verdify-db Service is in-cluster only).
#                       VERDIFY_KUBECTL may be a multi-token remote driver, e.g.
#                       "ssh jason@192.168.30.32 sudo k3s kubectl".
# VERDIFY_DB_BACKEND is the documented top-level knob. It is implemented on top
# of the lower-level VERDIFY_PSQL_MODE (below): docker -> docker-exec,
# dsn -> direct. If a caller sets VERDIFY_PSQL_MODE explicitly it still wins
# (back-compat with the call sites migrated before this knob existed).
#
# Modes (VERDIFY_PSQL_MODE) — lower-level, kept for back-compat:
#   docker-exec  (default) — docker exec [-i] <container> psql -U <user> -d <db>
#   direct                 — psql against PG* / VERDIFY_DB_* env (in-cluster /
#                            host with a reachable socket or TCP endpoint)
#   in-cluster             — alias of direct; reads DB_HOST/DB_PORT/DB_NAME/
#                            DB_USER/POSTGRES_PASSWORD (the k3s ConfigMap+Secret
#                            contract) and exports PG* for psql.
#
# Env knobs (all optional; defaults preserve VM behavior):
#   VERDIFY_DB_BACKEND      docker | dsn | kube                   (default docker)
#   VERDIFY_PSQL_MODE       docker-exec | direct | kube-exec      (overrides backend)
#   VERDIFY_DB_CONTAINER    docker container name   (default verdify-timescaledb)
#   VERDIFY_DB_USER         db user                 (default verdify)
#   VERDIFY_DB_NAME         db name                 (default verdify)
#   VERDIFY_DOCKER_STDIN    1 to add `docker exec -i` (for piped/heredoc SQL)
#   --- kube-exec backend (#254) ---
#   VERDIFY_KUBECTL         kubectl binary or remote driver       (default kubectl)
#   VERDIFY_DB_NAMESPACE    k3s namespace           (default verdify-prod)
#   VERDIFY_DB_POD          DB pod                  (default verdify-db-0)
#   VERDIFY_DB_PGCONTAINER  pod container w/ psql   (default postgres)
#
# The function intentionally does NOT bake -t/-A/-F/-c etc.: callers pass those
# so each site keeps its exact formatting flags.

# Guard against double-sourcing.
if [[ -n "${_VERDIFY_PSQL_LIB_LOADED:-}" ]]; then
    return 0 2>/dev/null || true
fi
_VERDIFY_PSQL_LIB_LOADED=1

# Resolve the active mode. VERDIFY_PSQL_MODE (explicit, lower-level) wins for
# back-compat; otherwise map the issue-#24 VERDIFY_DB_BACKEND knob (docker|dsn)
# onto it. Unset/empty backend keeps the docker-exec default -> byte-identical VM.
_VERDIFY_PSQL_MODE_DEFAULT="docker-exec"
if [[ -z "${VERDIFY_PSQL_MODE:-}" ]]; then
    case "${VERDIFY_DB_BACKEND:-docker}" in
        docker|docker-exec) VERDIFY_PSQL_MODE="docker-exec" ;;
        dsn|direct|in-cluster) VERDIFY_PSQL_MODE="direct" ;;
        kube|kubectl|kube-exec) VERDIFY_PSQL_MODE="kube-exec" ;;
        *)
            echo "psql-verdify.sh: unknown VERDIFY_DB_BACKEND='${VERDIFY_DB_BACKEND}' (use docker|dsn|kube)" >&2
            VERDIFY_PSQL_MODE="$_VERDIFY_PSQL_MODE_DEFAULT"
            ;;
    esac
fi
VERDIFY_PSQL_MODE="${VERDIFY_PSQL_MODE:-$_VERDIFY_PSQL_MODE_DEFAULT}"
VERDIFY_DB_CONTAINER="${VERDIFY_DB_CONTAINER:-verdify-timescaledb}"
VERDIFY_DB_USER="${VERDIFY_DB_USER:-${DB_USER:-verdify}}"
VERDIFY_DB_NAME="${VERDIFY_DB_NAME:-${DB_NAME:-verdify}}"

# kube-exec backend knobs (#254 — re-home the VM-era `docker exec verdify-
# timescaledb` firmware preflight DB handle to the k3s prod DB from a tooling
# host that has kubectl, NOT a Postgres socket). Defaults target verdify-prod/
# verdify-db-0 (container `postgres`). VERDIFY_KUBECTL lets the call run the
# binary verbatim OR through a remote driver, e.g.
#   VERDIFY_KUBECTL="ssh jason@192.168.30.32 sudo k3s kubectl"
# (it is word-split intentionally so a multi-token driver works).
VERDIFY_KUBECTL="${VERDIFY_KUBECTL:-kubectl}"
VERDIFY_DB_NAMESPACE="${VERDIFY_DB_NAMESPACE:-verdify-prod}"
VERDIFY_DB_POD="${VERDIFY_DB_POD:-verdify-db-0}"
VERDIFY_DB_PGCONTAINER="${VERDIFY_DB_PGCONTAINER:-postgres}"

# _verdify_pgoptions_from_extra — pull a `-e PGOPTIONS=...` pair out of the
# docker-extra args a call site passes to verdify_psql_cmd (e.g. the firmware
# preflight's statement_timeout). In docker-exec mode those args are forwarded
# verbatim to `docker exec`; the direct/kube-exec backends have no docker, so we
# translate the PGOPTIONS into an in-band `env PGOPTIONS=...` so the statement
# timeout is NOT silently dropped (the latent bug #254 calls out). Echoes the
# bare PGOPTIONS value (no `-e`/key); empty if none present.
_verdify_pgoptions_from_extra() {
    local prev="" f
    for f in "$@"; do
        if [[ "$prev" == "-e" && "$f" == PGOPTIONS=* ]]; then
            printf '%s' "${f#PGOPTIONS=}"
            return 0
        fi
        prev="$f"
    done
}

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
            # #254: don't drop a caller's `-e PGOPTIONS=...` (e.g. the planner
            # context statement_timeout). This function is normally consumed by
            # `mapfile < <(verdify_psql_cmd ...)`, so an export here occurs in a
            # process-substitution subshell and cannot reach the eventual psql
            # process. Emit `env PGOPTIONS=... psql` as command argv instead.
            local _pgopt
            _pgopt="$(_verdify_pgoptions_from_extra "${docker_extra[@]}")"
            if [[ -n "$_pgopt" && -z "${PGOPTIONS:-}" ]]; then
                printf '%s\n' env "PGOPTIONS=${_pgopt}"
            fi
            printf '%s\n' psql
            ;;
        kube-exec)
            # #254: re-home the VM `docker exec verdify-timescaledb psql ...` to
            # `<kubectl> exec [-i] -n <ns> <pod> -c <container> -- psql ...`. The
            # firmware preflight (and any other docker-exec call site) becomes
            # mode-agnostic: only the connection PREFIX changes, the psql flags +
            # SQL the call site appends are untouched. VERDIFY_KUBECTL is word-
            # split so a multi-token remote driver (ssh ... sudo k3s kubectl)
            # works. A `-e PGOPTIONS=...` from docker_extra is translated into an
            # in-pod `env PGOPTIONS=...` so statement_timeout survives.
            local _kbin
            # shellcheck disable=SC2206  # intentional word-split of the driver
            local -a _kctl=($VERDIFY_KUBECTL)
            printf '%s\n' "${_kctl[@]}"
            printf '%s\n' exec
            if [[ "${VERDIFY_DOCKER_STDIN:-0}" == "1" ]]; then
                printf '%s\n' -i
            fi
            printf '%s\n' -n "$VERDIFY_DB_NAMESPACE" "$VERDIFY_DB_POD" \
                -c "$VERDIFY_DB_PGCONTAINER" --
            local _kpgopt
            _kpgopt="$(_verdify_pgoptions_from_extra "${docker_extra[@]}")"
            if [[ -n "$_kpgopt" ]]; then
                printf '%s\n' env "PGOPTIONS=${_kpgopt}"
            fi
            printf '%s\n' psql -U "$VERDIFY_DB_USER" -d "$VERDIFY_DB_NAME"
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

# verdify_pg_program_cmd <program> [docker-extra...] — print the connection-prefix
# argv (one token per line) for ANY Postgres client other than psql (pg_dump,
# pg_restore, an interactive shell, ...). Same mode switch as verdify_psql_cmd:
#   docker-exec — docker exec [-i] <container> <program>
#   direct      — exports PG* (so libpq picks up host/port/db/user/password) and
#                 emits a bare <program> name.
# The DB connection flags (-U/-d) are baked in docker-exec mode to match the VM;
# in direct mode they come from PG*, so the caller adds only program flags.
#
#   mapfile -t DUMP < <(verdify_pg_program_cmd pg_dump)
#   "${DUMP[@]}" -Fc verdify > out.dump
verdify_pg_program_cmd() {
    local program="${1:?verdify_pg_program_cmd: program name required}"
    shift
    local -a docker_extra=("$@")
    case "$VERDIFY_PSQL_MODE" in
        docker-exec)
            printf '%s\n' docker exec
            if [[ "${VERDIFY_DOCKER_STDIN:-0}" == "1" ]]; then
                printf '%s\n' -i
            fi
            if [[ "${VERDIFY_DOCKER_TTY:-0}" == "1" ]]; then
                printf '%s\n' -t
            fi
            local f
            for f in "${docker_extra[@]}"; do
                printf '%s\n' "$f"
            done
            printf '%s\n' "$VERDIFY_DB_CONTAINER" "$program" -U "$VERDIFY_DB_USER" -d "$VERDIFY_DB_NAME"
            ;;
        direct|in-cluster)
            # Reuse the PG* export side-effect from verdify_psql_cmd, then emit a
            # bare program name. libpq clients (pg_dump etc.) read PG*.
            verdify_psql_cmd >/dev/null || return $?
            printf '%s\n' "$program"
            ;;
        kube-exec)
            # #254: `<kubectl> exec [-i][-t] -n <ns> <pod> -c <container> --
            # <program> -U <user> -d <db>`. -U/-d baked to match the docker-exec
            # contract (the pod's libpq has no PG* from the caller's host).
            local -a _kctl=($VERDIFY_KUBECTL)
            printf '%s\n' "${_kctl[@]}"
            printf '%s\n' exec
            if [[ "${VERDIFY_DOCKER_STDIN:-0}" == "1" ]]; then
                printf '%s\n' -i
            fi
            if [[ "${VERDIFY_DOCKER_TTY:-0}" == "1" ]]; then
                printf '%s\n' -t
            fi
            printf '%s\n' -n "$VERDIFY_DB_NAMESPACE" "$VERDIFY_DB_POD" \
                -c "$VERDIFY_DB_PGCONTAINER" -- \
                "$program" -U "$VERDIFY_DB_USER" -d "$VERDIFY_DB_NAME"
            ;;
        *)
            echo "psql-verdify.sh: unknown VERDIFY_PSQL_MODE='$VERDIFY_PSQL_MODE'" >&2
            return 2
            ;;
    esac
}

# ============================================================================
# READ-ONLY PARITY LAYER (pv_*) — used by scripts/db-parity.sh (#129).
# ============================================================================
# Sourceable helper. It NEVER writes: every session is opened with
# `default_transaction_read_only=on` and a statement timeout, so even an
# accidental DDL/DML in a caller aborts. It is the single connection seam used
# by scripts/db-parity.sh and any other read-only parity / audit tooling.
#
# Usage:
#   source "$(dirname "$0")/lib/psql-verdify.sh"
#   pv_target_init iris              # pick a named target (iris|target|<dsn>)
#   val=$(pv_psql_val "SELECT count(*) FROM climate;")
#   pv_psql "SELECT 1;"              # tab-separated, unaligned, tuples-only
#
# Two transport modes, auto-selected per target:
#   1. Docker container (default): `docker exec <container> psql -U <user> -d <db>`
#      Vars: VERDIFY_DB_CONTAINER (default verdify-timescaledb),
#            VERDIFY_DB_USER (default verdify), VERDIFY_DB_NAME (default verdify)
#   2. Direct DSN: a libpq URI / conninfo string (for staging or a disposable
#      fixture that is not the live docker container).
#      Provide via VERDIFY_DB_DSN, or pass a string containing "://" or "="
#      to pv_target_init.
#
# Named targets let db-parity.sh reference an "iris" (source of truth) side and
# a "TARGET" side without hard-coding either:
#   VERDIFY_IRIS_DSN / VERDIFY_IRIS_CONTAINER   -> the iris/source database
#   VERDIFY_TARGET_DSN / VERDIFY_TARGET_CONTAINER -> the candidate database
#
# No secret values are echoed by this library; DSNs are referenced, not printed.
#
# This layer is ADDITIVE on top of the verdify_* wrapper above. The verdify_*
# functions remain the general (read/write-capable) DB seam with the
# VERDIFY_DB_BACKEND=docker|dsn switch; the pv_* functions below are a
# strictly READ-ONLY seam for parity/audit tooling. Double-sourcing is already
# guarded by _VERDIFY_PSQL_LIB_LOADED at the top of this file.

# Read-only enforcement + a default statement timeout (ms). Callers may raise
# the timeout for heavy COUNT(*) on large hypertables.
PV_DB_STATEMENT_TIMEOUT_MS="${VERDIFY_DB_STATEMENT_TIMEOUT_MS:-30000}"
PV_PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=${PV_DB_STATEMENT_TIMEOUT_MS} -c idle_in_transaction_session_timeout=60000"

# Active transport state, set by pv_target_init.
PV_MODE=""        # "docker" | "dsn"
PV_DSN=""         # libpq conninfo when PV_MODE=dsn
PV_CONTAINER=""   # container name when PV_MODE=docker
PV_USER=""
PV_DB=""

# pv_target_init <name|dsn>
#   name "iris"   -> VERDIFY_IRIS_*   (defaults to docker verdify-timescaledb)
#   name "target" -> VERDIFY_TARGET_* (defaults to docker verdify-timescaledb)
#   any string containing "://" or "=" -> treated as a direct DSN
#   anything else -> treated as a docker container name
pv_target_init() {
  local sel="${1:-iris}"
  PV_MODE=""; PV_DSN=""; PV_CONTAINER=""; PV_USER=""; PV_DB=""

  case "$sel" in
    iris)
      if [ -n "${VERDIFY_IRIS_DSN:-}" ]; then
        PV_MODE="dsn"; PV_DSN="$VERDIFY_IRIS_DSN"
      else
        PV_MODE="docker"
        PV_CONTAINER="${VERDIFY_IRIS_CONTAINER:-${VERDIFY_DB_CONTAINER:-verdify-timescaledb}}"
      fi
      ;;
    target)
      if [ -n "${VERDIFY_TARGET_DSN:-}" ]; then
        PV_MODE="dsn"; PV_DSN="$VERDIFY_TARGET_DSN"
      elif [ -n "${VERDIFY_TARGET_CONTAINER:-}" ]; then
        PV_MODE="docker"; PV_CONTAINER="$VERDIFY_TARGET_CONTAINER"
      else
        # No explicit target -> fall back to the same docker default. db-parity.sh
        # warns when iris and target resolve to the same endpoint.
        PV_MODE="docker"
        PV_CONTAINER="${VERDIFY_DB_CONTAINER:-verdify-timescaledb}"
      fi
      ;;
    *://*|*=*)
      PV_MODE="dsn"; PV_DSN="$sel"
      ;;
    *)
      PV_MODE="docker"; PV_CONTAINER="$sel"
      ;;
  esac

  PV_USER="${VERDIFY_DB_USER:-verdify}"
  PV_DB="${VERDIFY_DB_NAME:-verdify}"
}

# Human-readable endpoint label (never includes credentials).
pv_target_label() {
  case "$PV_MODE" in
    docker) printf 'docker:%s/%s' "$PV_CONTAINER" "$PV_DB" ;;
    dsn)
      # Strip any "user:pass@" and query string so secrets are not printed.
      local d="$PV_DSN"
      d="${d##*@}"
      d="${d%%\?*}"
      printf 'dsn:%s' "$d"
      ;;
    *) printf 'uninitialized' ;;
  esac
}

# A stable identity used to detect "iris and target are the same endpoint".
pv_target_fingerprint() {
  case "$PV_MODE" in
    docker) printf 'docker:%s:%s:%s' "$PV_CONTAINER" "$PV_USER" "$PV_DB" ;;
    dsn)    printf 'dsn:%s' "$PV_DSN" ;;
    *)      printf 'none' ;;
  esac
}

# pv_psql <sql>  -> tuples-only, unaligned, tab-field output (read-only).
pv_psql() {
  local sql="$1"
  case "$PV_MODE" in
    docker)
      docker exec -e "PGOPTIONS=${PV_PGOPTIONS}" "$PV_CONTAINER" \
        psql -U "$PV_USER" -d "$PV_DB" -X -q -t -A -F $'\t' -c "$sql"
      ;;
    dsn)
      PGOPTIONS="${PV_PGOPTIONS}" psql "$PV_DSN" \
        -X -q -t -A -F $'\t' -c "$sql"
      ;;
    *)
      echo "pv_psql: target not initialized (call pv_target_init first)" >&2
      return 2
      ;;
  esac
}

# pv_psql_val <sql> -> single scalar, whitespace-trimmed. Empty string on no row.
pv_psql_val() {
  pv_psql "$1" | head -n1 | tr -d '[:space:]'
}

# pv_check_conn -> 0 if the target answers SELECT 1 read-only, else non-zero.
pv_check_conn() {
  local v
  v="$(pv_psql_val 'SELECT 1;' 2>/dev/null || true)"
  [ "$v" = "1" ]
}
