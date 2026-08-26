#!/usr/bin/env bash
# Read-only evidence capture for the e0be-derived Verdify combined release.
#
# Kubernetes reads are deliberately limited to exact Jobs, Pods, Deployments,
# one StatefulSet, four non-secret ConfigMap keys, and pod logs. This script
# never reads Secrets or full ConfigMaps, and performs no ArgoCD/Kubernetes
# mutation.
set -Eeuo pipefail

readonly SAFE_ROOT="/workspace/verdify-platform/scratch/tmp/live-combined-evidence"
readonly NAMESPACE="verdify-prod"
readonly API_HEALTH_URL="https://api.verdify.ai/health"
readonly MCP_READY_URL="https://mcp.verdify.ai/readyz"
readonly RESTORE_TIMESCALE_DIGEST="0af03ecf697825f6ddae76fd275d16bf46007bed6d00eb3d754779cb7db96fa6"

MODE="capture"
RELEASE=""
OUT=""
TIMEOUT_SECONDS=5400
ROLLOUT_TIMEOUT_SECONDS=1200
POLL_SECONDS=2
ACCEPT_EXISTING=0
EXPECTED_API_DIGEST=""
EXPECTED_MCP_DIGEST=""
EXPECTED_INGESTOR_DIGEST=""
EXPECTED_MIGRATE_DIGEST=""
EXPECTED_ORCHESTRATOR_DIGEST=""

usage() {
  cat <<'USAGE'
Usage:
  capture-live-combined-evidence.sh --dry-run --release <40-hex-sha> --output <dir>

  capture-live-combined-evidence.sh --release <40-hex-sha> --output <dir> \
    --api-digest <sha256> --mcp-digest <sha256> \
    --ingestor-digest <sha256> --migrate-digest <sha256> \
    --orchestrator-digest <sha256> [options]

Options:
  --dry-run                   Snapshot current state and exit without waiting.
  --accept-existing           Accept a hook already present when capture starts.
                              Default is to require a new UID; start before sync.
  --timeout-seconds N         Hook deadline (default 5400).
  --rollout-timeout-seconds N Workload/health deadline (default 1200).
  --poll-seconds N            Poll interval, 1..30 (default 2).

The output directory must be a new child of:
  /workspace/verdify-platform/scratch/tmp/live-combined-evidence
USAGE
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

normalize_digest() {
  local value="$1"
  value="${value#sha256:}"
  [[ "$value" =~ ^[0-9a-f]{64}$ ]] || return 1
  printf '%s' "$value"
}

require_uint_range() {
  local label="$1" value="$2" minimum="$3" maximum="$4"
  [[ "$value" =~ ^[0-9]+$ ]] || die "$label must be an integer"
  (( value >= minimum && value <= maximum )) \
    || die "$label must be between $minimum and $maximum"
}

while (( $# > 0 )); do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --release) [[ $# -ge 2 ]] || die "--release needs a value"; RELEASE="$2"; shift 2 ;;
    --output) [[ $# -ge 2 ]] || die "--output needs a value"; OUT="$2"; shift 2 ;;
    --api-digest) [[ $# -ge 2 ]] || die "--api-digest needs a value"; EXPECTED_API_DIGEST="$2"; shift 2 ;;
    --mcp-digest) [[ $# -ge 2 ]] || die "--mcp-digest needs a value"; EXPECTED_MCP_DIGEST="$2"; shift 2 ;;
    --ingestor-digest) [[ $# -ge 2 ]] || die "--ingestor-digest needs a value"; EXPECTED_INGESTOR_DIGEST="$2"; shift 2 ;;
    --migrate-digest) [[ $# -ge 2 ]] || die "--migrate-digest needs a value"; EXPECTED_MIGRATE_DIGEST="$2"; shift 2 ;;
    --orchestrator-digest) [[ $# -ge 2 ]] || die "--orchestrator-digest needs a value"; EXPECTED_ORCHESTRATOR_DIGEST="$2"; shift 2 ;;
    --timeout-seconds) [[ $# -ge 2 ]] || die "--timeout-seconds needs a value"; TIMEOUT_SECONDS="$2"; shift 2 ;;
    --rollout-timeout-seconds) [[ $# -ge 2 ]] || die "--rollout-timeout-seconds needs a value"; ROLLOUT_TIMEOUT_SECONDS="$2"; shift 2 ;;
    --poll-seconds) [[ $# -ge 2 ]] || die "--poll-seconds needs a value"; POLL_SECONDS="$2"; shift 2 ;;
    --accept-existing) ACCEPT_EXISTING=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$RELEASE" =~ ^[0-9a-f]{40}$ ]] || die "--release must be an exact lowercase 40-hex commit"
[[ -n "$OUT" ]] || die "--output is required"
require_uint_range "--timeout-seconds" "$TIMEOUT_SECONDS" 60 14400
require_uint_range "--rollout-timeout-seconds" "$ROLLOUT_TIMEOUT_SECONDS" 60 3600
require_uint_range "--poll-seconds" "$POLL_SECONDS" 1 30

OUT="$(readlink -m -- "$OUT")"
case "$OUT" in
  "$SAFE_ROOT"/*) ;;
  *) die "--output must be a child of $SAFE_ROOT" ;;
esac
[[ ! -e "$OUT" ]] || die "refusing to overwrite existing output: $OUT"

if [[ "$MODE" == "capture" ]]; then
  EXPECTED_API_DIGEST="$(normalize_digest "$EXPECTED_API_DIGEST")" \
    || die "--api-digest must be a sha256 digest"
  EXPECTED_MCP_DIGEST="$(normalize_digest "$EXPECTED_MCP_DIGEST")" \
    || die "--mcp-digest must be a sha256 digest"
  EXPECTED_INGESTOR_DIGEST="$(normalize_digest "$EXPECTED_INGESTOR_DIGEST")" \
    || die "--ingestor-digest must be a sha256 digest"
  EXPECTED_MIGRATE_DIGEST="$(normalize_digest "$EXPECTED_MIGRATE_DIGEST")" \
    || die "--migrate-digest must be a sha256 digest"
  EXPECTED_ORCHESTRATOR_DIGEST="$(normalize_digest "$EXPECTED_ORCHESTRATOR_DIGEST")" \
    || die "--orchestrator-digest must be a sha256 digest"
fi

for command_name in kubectl curl jq sha256sum date readlink grep awk; do
  command -v "$command_name" >/dev/null 2>&1 \
    || die "required command not found: $command_name"
done

umask 077
mkdir -p -- "$OUT/hooks"
chmod 0700 "$OUT" "$OUT/hooks"

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
STARTED_EPOCH="$(date -u +%s)"
readonly STARTED_AT STARTED_EPOCH

cat >"$OUT/run.meta" <<EOF
mode=$MODE
namespace=$NAMESPACE
release=$RELEASE
started_at=$STARTED_AT
timeout_seconds=$TIMEOUT_SECONDS
rollout_timeout_seconds=$ROLLOUT_TIMEOUT_SECONDS
poll_seconds=$POLL_SECONDS
accept_existing=$ACCEPT_EXISTING
expected_api_digest=${EXPECTED_API_DIGEST:-not-required-in-dry-run}
expected_mcp_digest=${EXPECTED_MCP_DIGEST:-not-required-in-dry-run}
expected_ingestor_digest=${EXPECTED_INGESTOR_DIGEST:-not-required-in-dry-run}
expected_migrate_digest=${EXPECTED_MIGRATE_DIGEST:-not-required-in-dry-run}
expected_orchestrator_digest=${EXPECTED_ORCHESTRATOR_DIGEST:-not-required-in-dry-run}
cluster_operations=read-only
secret_api_calls=none
configmap_scope=verdify-config/four-explicit-non-secret-keys-only
argocd_operations=none
EOF

k() {
  kubectl -n "$NAMESPACE" "$@"
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

readonly -a HOOK_KEYS=(restore migrate runtime_bootstrap experiment_bootstrap)
declare -A JOB_NAME=(
  [restore]="verdify-experiment-v2-restore-rehearsal"
  [migrate]="verdify-migrate"
  [runtime_bootstrap]="verdify-runtime-role-bootstrap"
  [experiment_bootstrap]="verdify-experiment-v2-credential-bootstrap"
)
declare -A MAIN_CONTAINER=(
  [restore]="restore-and-verify"
  [migrate]="migrate"
  [runtime_bootstrap]="bootstrap-and-attest"
  [experiment_bootstrap]="bootstrap-and-attest"
)
declare -A BASELINE_UID=()

job_uid() {
  k get job "$1" -o jsonpath='{.metadata.uid}' 2>/dev/null || true
}

snapshot_hook_baseline() {
  local key job uid
  printf 'captured_at\thook\tjob\tuid\n' >"$OUT/hooks.baseline.tsv"
  for key in "${HOOK_KEYS[@]}"; do
    job="${JOB_NAME[$key]}"
    uid="$(job_uid "$job")"
    BASELINE_UID[$key]="$uid"
    printf '%s\t%s\t%s\t%s\n' \
      "$(now_utc)" "$key" "$job" "${uid:-ABSENT}" \
      >>"$OUT/hooks.baseline.tsv"
  done
}

snapshot_feature_flags() {
  local label="$1" values
  values="$(k get configmap verdify-config \
    -o jsonpath='{.data.VERDIFY_COMPONENT_EXPERIMENT_ENABLED}{"\t"}{.data.VERDIFY_POLICY_VECTOR_MODE}{"\t"}{.data.VERDIFY_ACTIVE_EXPERIMENT_ID}{"\t"}{.data.VERDIFY_DEVICE_WRITE_ENABLED}{"\n"}' \
    2>"$OUT/feature-flags.${label}.stderr")" || return 1
  {
    printf 'captured_at\tcomponent_experiment\tpolicy_vector_mode\tactive_experiment_id\tdevice_write_enabled\n'
    printf '%s\t%s\n' "$(now_utc)" "$values"
  } >"$OUT/feature-flags.${label}.tsv"
}

snapshot_deployment() {
  local name="$1" destination="$2" row
  if row="$(k get deployment "$name" -o jsonpath='{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.creationTimestamp}{"\t"}{.metadata.generation}{"\t"}{.status.observedGeneration}{"\t"}{.spec.replicas}{"\t"}{.status.updatedReplicas}{"\t"}{.status.readyReplicas}{"\t"}{.status.availableReplicas}{"\t"}{.status.unavailableReplicas}{"\t"}{.spec.strategy.type}{"\t"}{.spec.template.spec.containers[0].name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}' 2>/dev/null)"; then
    printf '%s\tDeployment\t%s\n' "$(now_utc)" "$row" >>"$destination"
  else
    printf '%s\tDeployment\t%s\tABSENT\n' "$(now_utc)" "$name" >>"$destination"
  fi
}

snapshot_statefulset() {
  local name="$1" destination="$2" row
  if row="$(k get statefulset "$name" -o jsonpath='{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.creationTimestamp}{"\t"}{.metadata.generation}{"\t"}{.status.observedGeneration}{"\t"}{.spec.replicas}{"\t"}{.status.updatedReplicas}{"\t"}{.status.readyReplicas}{"\t"}{.status.currentRevision}{"\t"}{.status.updateRevision}{"\t"}{.spec.updateStrategy.type}{"\t"}{.spec.template.spec.containers[0].name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}' 2>/dev/null)"; then
    printf '%s\tStatefulSet\t%s\n' "$(now_utc)" "$row" >>"$destination"
  else
    printf '%s\tStatefulSet\t%s\tABSENT\n' "$(now_utc)" "$name" >>"$destination"
  fi
}

snapshot_component_pods() {
  local component="$1" destination="$2"
  local rows
  rows="$(k get pods -l "app.kubernetes.io/component=${component}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.ownerReferences[0].kind}{"/"}{.metadata.ownerReferences[0].name}{"/"}{.metadata.ownerReferences[0].uid}{"\t"}{.metadata.creationTimestamp}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\t"}{.status.startTime}{"\t"}{.metadata.deletionTimestamp}{"\t"}{range .status.initContainerStatuses[*]}init:{.name}|ready={.ready}|restarts={.restartCount}|image={.image}|imageID={.imageID}|state={.state.waiting.reason}{.state.terminated.reason}{.state.running.startedAt};{end}{"\t"}{range .status.containerStatuses[*]}main:{.name}|ready={.ready}|restarts={.restartCount}|image={.image}|imageID={.imageID}|state={.state.waiting.reason}{.state.terminated.reason}{.state.running.startedAt}|exit={.state.terminated.exitCode};{end}{"\n"}{end}' \
    2>/dev/null || true)"
  if [[ -z "$rows" ]]; then
    printf '%s\t%s\tABSENT\n' "$(now_utc)" "$component" >>"$destination"
  else
    while IFS= read -r row; do
      [[ -n "$row" ]] || continue
      printf '%s\t%s\t%s\n' "$(now_utc)" "$component" "$row" >>"$destination"
    done <<<"$rows"
  fi
}

snapshot_workloads() {
  local label="$1"
  local resources="$OUT/workloads.${label}.resources.tsv"
  local pods="$OUT/workloads.${label}.pods.tsv"
  printf 'captured_at\tkind\tname\tuid\tcreated\tgeneration\tobserved_generation\tdesired\tupdated_or_current\tready\tavailable_or_current_revision\tunavailable_or_update_revision\tstrategy\tcontainer\timage\n' >"$resources"
  local deployment
  for deployment in \
    verdify-api verdify-mcp verdify-ingestor \
    experiment-v2-lifecycle experiment-v2-selector experiment-v2-freezer
  do
    snapshot_deployment "$deployment" "$resources"
  done
  snapshot_statefulset verdify-db "$resources"

  printf 'captured_at\tcomponent\tpod\tuid\towner\tcreated\tnode\tphase\tstarted\tdeletion_timestamp\tinit_statuses\tmain_statuses\n' >"$pods"
  local component
  for component in \
    api mcp ingestor db \
    experiment-v2-lifecycle experiment-v2-selector experiment-v2-freezer
  do
    snapshot_component_pods "$component" "$pods"
  done
}

capture_api_health() {
  local label="$1" captured raw
  captured="$(now_utc)"
  raw="$(curl -fsS --max-time 10 "$API_HEALTH_URL")" || return 1
  jq --arg captured_at "$captured" --arg endpoint "$API_HEALTH_URL" \
    '{captured_at:$captured_at,endpoint:$endpoint,status:.status,checks:{active_alerts_1h:.checks.active_alerts_1h,climate_action_log_age_seconds:.checks.climate_action_log_age_seconds,climate_action_log_proof_missing:.checks.climate_action_log_proof_missing,climate_age_seconds:.checks.climate_age_seconds,greenhouse_mode:.checks.greenhouse_mode,last_setpoint_change_seconds:.checks.last_setpoint_change_seconds,service_climate_action_log:.checks.service_climate_action_log,service_ingestor:.checks.service_ingestor}}' \
    <<<"$raw" >"$OUT/health.${label}.api.json"
}

capture_mcp_health() {
  local label="$1" captured raw
  captured="$(now_utc)"
  raw="$(curl -fsS --max-time 10 "$MCP_READY_URL")" || return 1
  jq --arg captured_at "$captured" --arg endpoint "$MCP_READY_URL" \
    '{captured_at:$captured_at,endpoint:$endpoint,ready:.ready,db:.db,db_error_class:.db_error_class,auth_mode:.auth_mode,auth_audiences_configured:.auth_audiences_configured,auth_misconfigured:.auth_misconfigured,auth_unrecognized_token_envs:.auth_unrecognized_token_envs,missing_tools:.missing_tools,required_tools:.required_tools}' \
    <<<"$raw" >"$OUT/health.${label}.mcp.json"
}

capture_public_health() {
  local label="$1"
  capture_api_health "$label" || return 1
  capture_mcp_health "$label" || return 1
}

job_snapshot_line() {
  local job="$1"
  k get job "$job" -o jsonpath='{.metadata.uid}{"\t"}{.metadata.creationTimestamp}{"\t"}{.status.startTime}{"\t"}{.status.completionTime}{"\t"}{.status.active}{"\t"}{.status.succeeded}{"\t"}{.status.failed}{"\t"}{range .status.conditions[*]}{.type}={.status}:{.reason}:{.lastTransitionTime};{end}{"\t"}{.spec.ttlSecondsAfterFinished}{"\t"}{.spec.activeDeadlineSeconds}{"\t"}{range .spec.template.spec.initContainers[*]}init:{.name}={.image};{end}{"\t"}{range .spec.template.spec.containers[*]}main:{.name}={.image};{end}{"\n"}'
}

find_owned_pod() {
  local job="$1" owner_uid="$2" name _pod_uid owner _created selected=""
  while IFS=$'\t' read -r name _pod_uid owner _created; do
    [[ -n "$name" ]] || continue
    if [[ "$owner" == "$owner_uid" ]]; then
      selected="$name"
      break
    fi
  done < <(k get pods -l "job-name=${job}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.ownerReferences[0].uid}{"\t"}{.metadata.creationTimestamp}{"\n"}{end}' \
    2>/dev/null || true)
  printf '%s' "$selected"
}

pod_snapshot_line() {
  local pod="$1"
  k get pod "$pod" \
    -o jsonpath='{.metadata.name}{"\t"}{.metadata.uid}{"\t"}{.metadata.ownerReferences[0].kind}{"/"}{.metadata.ownerReferences[0].name}{"/"}{.metadata.ownerReferences[0].uid}{"\t"}{.metadata.creationTimestamp}{"\t"}{.spec.nodeName}{"\t"}{.status.phase}{"\t"}{.status.startTime}{"\t"}{.metadata.deletionTimestamp}{"\t"}{range .status.initContainerStatuses[*]}init:{.name}|ready={.ready}|restarts={.restartCount}|image={.image}|imageID={.imageID}|state={.state.waiting.reason}{.state.terminated.reason}{.state.running.startedAt}|exit={.state.terminated.exitCode};{end}{"\t"}{range .status.containerStatuses[*]}main:{.name}|ready={.ready}|restarts={.restartCount}|image={.image}|imageID={.imageID}|state={.state.waiting.reason}{.state.terminated.reason}{.state.running.startedAt}|exit={.state.terminated.exitCode};{end}{"\n"}'
}

container_started_or_terminated() {
  local pod="$1" container="$2" state
  state="$(k get pod "$pod" \
    -o jsonpath="{range .status.containerStatuses[?(@.name==\"${container}\")]}{.state.running.startedAt}{.state.terminated.finishedAt}{end}" \
    2>/dev/null || true)"
  [[ -n "$state" ]]
}

record_assertion() {
  local file="$1" assertion="$2" status="$3" detail="$4"
  printf '%s\t%s\t%s\t%s\n' "$(now_utc)" "$assertion" "$status" "$detail" >>"$file"
}

assert_fixed_marker() {
  local logfile="$1" assertions="$2" name="$3" marker="$4"
  if grep -Fq -- "$marker" "$logfile"; then
    record_assertion "$assertions" "$name" PASS "required fixed marker present"
  else
    record_assertion "$assertions" "$name" FAIL "required fixed marker absent"
    return 1
  fi
}

assert_no_fixed_marker() {
  local logfile="$1" assertions="$2" name="$3" marker="$4"
  if grep -Fq -- "$marker" "$logfile"; then
    record_assertion "$assertions" "$name" FAIL "forbidden failure marker present"
    return 1
  fi
  record_assertion "$assertions" "$name" PASS "forbidden failure marker absent"
}

assert_no_secret_assignment() {
  local logfile="$1" assertions="$2"
  # Do not print a matching line. If a job ever logs an obvious protected
  # assignment, quarantine the raw file and fail the evidence packet.
  if grep -Eqi '(POSTGRES_PASSWORD|DB_PASS|DB_PASSWORD|API_KEY|AUTHORIZATION|BEARER|_TOKEN)[[:space:]]*[:=][[:space:]]*[^[:space:]]+' "$logfile"; then
    chmod 000 "$logfile"
    record_assertion "$assertions" secret_assignment_guard FAIL \
      "potential protected assignment detected; raw log quarantined and not displayed"
    return 1
  fi
  record_assertion "$assertions" secret_assignment_guard PASS \
    "no obvious protected assignment syntax detected"
}

assert_migrate_receipts() {
  local logfile="$1" assertions="$2" entry filename expected_sha
  local -a migrations=(
    '214-confirmed-component-experiment-v2.sql:ac155aa5d6c02218e755e4ca7386e4477cf4b5791d9e1efef49c0dc427c12bda'
    '215-experiment-v2-ops-observability.sql:ebb4523f88f977d6038dd33a4828232f2097fdd904c7f922b7086a163d623974'
    '216-equipment-counter-source-ledger.sql:6cee4d1eec2fc2968d38e9f9581fbf88d3ec882022778b602564f0c59ecca73f'
    '217-runtime-role-boundary.sql:2fa8eac4104f5e6682a5d8101bad4ba8b83e818cf356bf006b340c6d717b1e33'
    '218-planner-required-failure-history.sql:1215deb2c2fbdd780d6e8a33be6c22921745ebd288231d8edb663fbeeced07dd'
  )
  assert_fixed_marker "$logfile" "$assertions" migrate_ledger_mode \
    '[migrate] VERDIFY_MIGRATE_LEDGER=1 — running ledgered migration delivery ...' || return 1
  assert_fixed_marker "$logfile" "$assertions" migrate_core_schema \
    '[migrate] verify OK — schema present.' || return 1
  if ! grep -Eq '\[apply-migrations\] (ledger is current — nothing to apply\.|done: applied [0-9]+ migration\(s\); ledger is current\.)' "$logfile"; then
    record_assertion "$assertions" migrate_ledger_current FAIL \
      "ledger-current terminal receipt absent"
    return 1
  fi
  record_assertion "$assertions" migrate_ledger_current PASS \
    "ledger-current terminal receipt present"

  for entry in "${migrations[@]}"; do
    filename="${entry%%:*}"
    expected_sha="${entry##*:}"
    if grep -Fq "pending: ${filename} sha256=${expected_sha}" "$logfile"; then
      if ! grep -Fq "applied + stamped: ${filename}" "$logfile"; then
        record_assertion "$assertions" "migration_${filename%%-*}" FAIL \
          "exact pending hash was not followed by applied-and-stamped receipt"
        return 1
      fi
      record_assertion "$assertions" "migration_${filename%%-*}" PASS \
        "exact a754 hash applied and stamped"
    elif grep -Fq "pending: ${filename} sha256=" "$logfile"; then
      record_assertion "$assertions" "migration_${filename%%-*}" FAIL \
        "pending receipt carried a non-a754 hash"
      return 1
    else
      # apply-migrations recomputes and compares every already-ledgered file;
      # any mismatch terminates with FATAL before the terminal current receipt.
      record_assertion "$assertions" "migration_${filename%%-*}" PASS \
        "already-ledgered exact hash validated by the runner's full SHA sweep (${expected_sha})"
    fi
  done
}

assert_hook_log() {
  local key="$1" logfile="$2" assertions="$3" ok=0
  printf 'captured_at\tassertion\tstatus\tdetail\n' >"$assertions"
  assert_no_secret_assignment "$logfile" "$assertions" || ok=1
  case "$key" in
    restore)
      assert_no_fixed_marker "$logfile" "$assertions" restore_fatal \
        '[restore-rehearsal] FATAL:' || ok=1
      assert_fixed_marker "$logfile" "$assertions" restore_ledger_current \
        'candidate migrations applied twice; exact candidate ledger is current' || ok=1
      assert_fixed_marker "$logfile" "$assertions" migration_217_timescale_fixture \
        'PASS: migration 217 Timescale/runtime boundary fixture' || ok=1
      assert_fixed_marker "$logfile" "$assertions" migration_217_exact_runtime_fixture \
        'migration 217 exact-runtime Timescale facade/ACL fixture passed' || ok=1
      assert_fixed_marker "$logfile" "$assertions" migration_218_failure_history_fixture \
        'migration 218 required-failure-history fixture passed' || ok=1
      assert_fixed_marker "$logfile" "$assertions" restore_final_pass \
        'PASS: recent-dump schema/ACL replay and v2 vertical fixtures' || ok=1
      ;;
    migrate)
      assert_no_fixed_marker "$logfile" "$assertions" migrate_fatal \
        '[migrate] FATAL:' || ok=1
      assert_no_fixed_marker "$logfile" "$assertions" apply_migrations_fatal \
        '[apply-migrations] FATAL:' || ok=1
      assert_migrate_receipts "$logfile" "$assertions" || ok=1
      ;;
    runtime_bootstrap)
      assert_no_fixed_marker "$logfile" "$assertions" runtime_bootstrap_failure \
        '[runtime-role-bootstrap] fail-closed:' || ok=1
      assert_fixed_marker "$logfile" "$assertions" runtime_bootstrap_receipt \
        '[runtime-role-bootstrap] both ordinary logins installed and attested' || ok=1
      ;;
    experiment_bootstrap)
      assert_no_fixed_marker "$logfile" "$assertions" experiment_bootstrap_failure \
        '[experiment-v2-credential-bootstrap] fail-closed:' || ok=1
      assert_fixed_marker "$logfile" "$assertions" experiment_bootstrap_receipt \
        '[experiment-v2-credential-bootstrap] six database logins installed and attested; API token shapes validated' || ok=1
      ;;
  esac
  return "$ok"
}

assert_hook_images() {
  local key="$1" pod="$2" assertions="$3" actual
  if [[ "$key" == "restore" ]]; then
    actual="$(k get pod "$pod" \
      -o jsonpath='{.status.initContainerStatuses[?(@.name=="candidate-migration-source")].imageID}' \
      2>/dev/null || true)"
    if [[ "$actual" == *"sha256:${EXPECTED_MIGRATE_DIGEST}"* ]]; then
      record_assertion "$assertions" restore_candidate_migrate_image PASS \
        "candidate init imageID matches expected migrate digest"
    else
      record_assertion "$assertions" restore_candidate_migrate_image FAIL \
        "candidate init imageID does not match expected migrate digest"
      return 1
    fi
    actual="$(k get pod "$pod" \
      -o jsonpath='{.status.containerStatuses[?(@.name=="restore-and-verify")].imageID}' \
      2>/dev/null || true)"
    if [[ "$actual" == *"sha256:${RESTORE_TIMESCALE_DIGEST}"* ]]; then
      record_assertion "$assertions" restore_timescale_image PASS \
        "restore main imageID matches pinned TimescaleDB 2.25.2-pg16 digest"
    else
      record_assertion "$assertions" restore_timescale_image FAIL \
        "restore main imageID does not match pinned TimescaleDB digest"
      return 1
    fi
  else
    actual="$(k get pod "$pod" \
      -o jsonpath="{.status.containerStatuses[?(@.name==\"${MAIN_CONTAINER[$key]}\")].imageID}" \
      2>/dev/null || true)"
    if [[ "$actual" == *"sha256:${EXPECTED_MIGRATE_DIGEST}"* ]]; then
      record_assertion "$assertions" migrate_image PASS \
        "main imageID matches expected migrate digest"
    else
      record_assertion "$assertions" migrate_image FAIL \
        "main imageID does not match expected migrate digest"
      return 1
    fi
  fi
}

assert_hook_pod_terminal() {
  local key="$1" pod="$2" assertions="$3" row phase restarts exit_code
  row="$(k get pod "$pod" \
    -o jsonpath="{.status.phase}{\"|\"}{.status.containerStatuses[?(@.name==\"${MAIN_CONTAINER[$key]}\")].restartCount}{\"|\"}{.status.containerStatuses[?(@.name==\"${MAIN_CONTAINER[$key]}\")].state.terminated.exitCode}" \
    2>/dev/null || true)"
  IFS='|' read -r phase restarts exit_code <<<"$row"
  if [[ "$phase" == "Succeeded" && "$restarts" == "0" && "$exit_code" == "0" ]]; then
    record_assertion "$assertions" pod_terminal_status PASS \
      "pod Succeeded; main container exit=0 and restartCount=0"
  else
    record_assertion "$assertions" pod_terminal_status FAIL \
      "pod/main terminal status did not match Succeeded, exit=0, restartCount=0"
    return 1
  fi

  local init_rows init_name init_restarts init_exit
  init_rows="$(k get pod "$pod" \
    -o jsonpath='{range .status.initContainerStatuses[*]}{.name}{"|"}{.restartCount}{"|"}{.state.terminated.exitCode}{"\n"}{end}' \
    2>/dev/null || true)"
  while IFS='|' read -r init_name init_restarts init_exit; do
    [[ -n "$init_name" ]] || continue
    if [[ "$init_restarts" != "0" || "$init_exit" != "0" ]]; then
      record_assertion "$assertions" init_terminal_status FAIL \
        "an init container did not terminate exit=0 with restartCount=0"
      return 1
    fi
  done <<<"$init_rows"
  record_assertion "$assertions" init_terminal_status PASS \
    "all present init containers terminated exit=0 with restartCount=0"
}

capture_hook() {
  local key="$1"
  local job="${JOB_NAME[$key]}" container="${MAIN_CONTAINER[$key]}"
  local dir="$OUT/hooks/$key" baseline="${BASELINE_UID[$key]}"
  local deadline=$(( STARTED_EPOCH + TIMEOUT_SECONDS ))
  local uid="" pod="" line="" last_line="" conditions="" log_pid=""
  local follow_log="$dir/main.follow.log" final_log="$dir/main.log"
  local follow_stderr="$dir/log-client.stderr" assertions="$dir/assertions.tsv"
  mkdir -p -- "$dir"
  chmod 0700 "$dir"
  printf 'captured_at\tuid\tcreated\tstarted\tcompleted\tactive\tsucceeded\tfailed\tconditions\tttl_seconds\tactive_deadline_seconds\tinit_images\tmain_images\n' \
    >"$dir/job-status.tsv"
  printf 'captured_at\tpod\tuid\towner\tcreated\tnode\tphase\tstarted\tdeletion_timestamp\tinit_statuses\tmain_statuses\n' \
    >"$dir/pod-status.tsv"

  while (( $(date -u +%s) <= deadline )); do
    uid="$(job_uid "$job")"
    if [[ -z "$uid" ]]; then
      if [[ -f "$OUT/abort" ]]; then
        printf 'BLOCKED\taborted before hook appeared because an earlier wave failed\n' >"$dir/result.tsv"
        return 1
      fi
      sleep "$POLL_SECONDS"
      continue
    fi
    if [[ "$ACCEPT_EXISTING" != "1" && -n "$baseline" && "$uid" == "$baseline" ]]; then
      sleep "$POLL_SECONDS"
      continue
    fi
    break
  done
  if [[ -z "$uid" || ( "$ACCEPT_EXISTING" != "1" && -n "$baseline" && "$uid" == "$baseline" ) ]]; then
    printf 'FAIL\thook did not appear with a new UID before timeout\n' >"$dir/result.tsv"
    printf '%s\t%s\n' "$key" "hook did not appear with a new UID" >"$OUT/abort"
    return 1
  fi
  printf '[%s] observed %s uid=%s\n' "$(now_utc)" "$job" "$uid"

  while (( $(date -u +%s) <= deadline )); do
    line="$(job_snapshot_line "$job" 2>/dev/null || true)"
    if [[ -z "$line" ]]; then
      printf 'FAIL\thook disappeared before terminal evidence capture\n' >"$dir/result.tsv"
      printf '%s\t%s\n' "$key" "hook disappeared before terminal evidence capture" >"$OUT/abort"
      return 1
    fi
    if [[ "$line" != "$last_line" ]]; then
      printf '%s\t%s\n' "$(now_utc)" "$line" >>"$dir/job-status.tsv"
      last_line="$line"
    fi

    if [[ -z "$pod" ]]; then
      pod="$(find_owned_pod "$job" "$uid")"
      if [[ -n "$pod" ]]; then
        pod_snapshot_line "$pod" | awk -v captured="$(now_utc)" '{print captured "\t" $0}' \
          >>"$dir/pod-status.tsv"
      fi
    fi

    if [[ -n "$pod" && -z "$log_pid" ]] \
      && container_started_or_terminated "$pod" "$container"; then
      k logs "$pod" --container "$container" --follow --timestamps \
        >"$follow_log" 2>"$follow_stderr" &
      log_pid="$!"
    fi

    conditions="${line//$'\t'/ }"
    if [[ "$conditions" == *"Failed=True:"* ]]; then
      if [[ -n "$pod" ]]; then
        pod_snapshot_line "$pod" | awk -v captured="$(now_utc)" '{print captured "\t" $0}' \
          >>"$dir/pod-status.tsv" || true
        k logs "$pod" --container "$container" --timestamps \
          >"$final_log" 2>>"$follow_stderr" || true
      fi
      printf 'FAIL\tJob entered Failed=True\n' >"$dir/result.tsv"
      printf '%s\t%s\n' "$key" "Job entered Failed=True" >"$OUT/abort"
      return 1
    fi
    if [[ "$conditions" == *"Complete=True:"* ]]; then
      break
    fi
    sleep "$POLL_SECONDS"
  done

  if [[ "$conditions" != *"Complete=True:"* ]]; then
    printf 'FAIL\tJob did not complete before timeout\n' >"$dir/result.tsv"
    printf '%s\t%s\n' "$key" "Job did not complete before timeout" >"$OUT/abort"
    return 1
  fi
  [[ -n "$pod" ]] || {
    printf 'FAIL\tJob completed but owned pod was not captured\n' >"$dir/result.tsv"
    printf '%s\t%s\n' "$key" "owned pod not captured" >"$OUT/abort"
    return 1
  }

  local _wait_round
  if [[ -n "$log_pid" ]]; then
    for _wait_round in {1..15}; do
      kill -0 "$log_pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$log_pid" 2>/dev/null; then
      kill "$log_pid" 2>/dev/null || true
    fi
    wait "$log_pid" 2>/dev/null || true
  fi

  # Prefer a final non-follow read while the TTL-bearing pod is known to exist;
  # the follow copy remains the fallback if the pod vanished at this instant.
  if ! k logs "$pod" --container "$container" --timestamps \
      >"$final_log" 2>>"$follow_stderr"; then
    if [[ -s "$follow_log" ]]; then
      cp -- "$follow_log" "$final_log"
    else
      printf 'FAIL\tmain-container log could not be captured\n' >"$dir/result.tsv"
      printf '%s\t%s\n' "$key" "main-container log unavailable" >"$OUT/abort"
      return 1
    fi
  fi
  chmod 0600 "$final_log" "$follow_log" "$follow_stderr" 2>/dev/null || true
  (
    cd "$dir"
    sha256sum main.log >main.log.sha256
  )
  pod_snapshot_line "$pod" | awk -v captured="$(now_utc)" '{print captured "\t" $0}' \
    >>"$dir/pod-status.tsv"

  local assertion_failed=0
  assert_hook_log "$key" "$final_log" "$assertions" || assertion_failed=1
  assert_hook_images "$key" "$pod" "$assertions" || assertion_failed=1
  assert_hook_pod_terminal "$key" "$pod" "$assertions" || assertion_failed=1
  if (( assertion_failed != 0 )); then
    printf 'FAIL\tJob completed but evidence assertions failed\n' >"$dir/result.tsv"
    printf '%s\t%s\n' "$key" "evidence assertions failed" >"$OUT/abort"
    return 1
  fi
  printf 'PASS\tJob Complete=True; required receipts and exact imageIDs captured\n' \
    >"$dir/result.tsv"
  printf '[%s] evidence PASS for %s\n' "$(now_utc)" "$job"
}

deployment_converged() {
  local name="$1" expected_digest="$2" row generation observed desired updated ready available image
  row="$(k get deployment "$name" \
    -o jsonpath='{.metadata.generation}{"\t"}{.status.observedGeneration}{"\t"}{.spec.replicas}{"\t"}{.status.updatedReplicas}{"\t"}{.status.readyReplicas}{"\t"}{.status.availableReplicas}{"\t"}{.spec.template.spec.containers[0].image}' \
    2>/dev/null)" || return 1
  IFS=$'\t' read -r generation observed desired updated ready available image <<<"$row"
  [[ -n "$generation" && "$generation" == "$observed" ]] || return 1
  [[ -n "$desired" && "$updated" == "$desired" && "$ready" == "$desired" && "$available" == "$desired" ]] || return 1
  [[ "$image" == *"@sha256:${expected_digest}" ]] || return 1
}

expected_pods_ready() {
  local component="$1" expected_digest="$2" expected_count="$3"
  local rows name ready restarts image_id deletion total=0 good=0
  rows="$(k get pods -l "app.kubernetes.io/component=${component}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].ready}{"\t"}{.status.containerStatuses[0].restartCount}{"\t"}{.status.containerStatuses[0].imageID}{"\t"}{.metadata.deletionTimestamp}{"\n"}{end}' \
    2>/dev/null || true)"
  while IFS=$'\t' read -r name ready restarts image_id deletion; do
    [[ -n "$name" ]] || continue
    [[ -z "$deletion" ]] || continue
    total=$(( total + 1 ))
    if [[ "$ready" == "true" && "$restarts" == "0" \
          && "$image_id" == *"sha256:${expected_digest}"* ]]; then
      good=$(( good + 1 ))
    fi
  done <<<"$rows"
  (( total == expected_count && good == expected_count ))
}

db_stable() {
  local sts_row pod_row sts_uid desired updated ready image
  local pod_uid pod_ready pod_restarts pod_image_id
  sts_row="$(k get statefulset verdify-db \
    -o jsonpath='{.metadata.uid}{"|"}{.spec.replicas}{"|"}{.status.updatedReplicas}{"|"}{.status.readyReplicas}{"|"}{.spec.template.spec.containers[0].image}' \
    2>/dev/null)" || return 1
  IFS='|' read -r sts_uid desired updated ready image <<<"$sts_row"
  [[ "$sts_uid" == "$BASELINE_DB_STS_UID" && "$desired" == "1" \
     && "$updated" == "1" && "$ready" == "1" \
     && "$image" == "timescale/timescaledb:2.25.2-pg16" ]] || return 1
  pod_row="$(k get pod verdify-db-0 \
    -o jsonpath='{.metadata.uid}{"|"}{.status.containerStatuses[?(@.name=="postgres")].ready}{"|"}{.status.containerStatuses[?(@.name=="postgres")].restartCount}{"|"}{.status.containerStatuses[?(@.name=="postgres")].imageID}' \
    2>/dev/null)" || return 1
  IFS='|' read -r pod_uid pod_ready pod_restarts pod_image_id <<<"$pod_row"
  [[ "$pod_uid" == "$BASELINE_DB_POD_UID" && "$pod_ready" == "true" \
     && "$pod_restarts" == "0" \
     && "$pod_image_id" == *"sha256:${RESTORE_TIMESCALE_DIGEST}"* ]]
}

ingestor_singleton_safe() {
  local row desired updated ready available strategy image
  row="$(k get deployment verdify-ingestor \
    -o jsonpath='{.spec.replicas}{"|"}{.status.updatedReplicas}{"|"}{.status.readyReplicas}{"|"}{.status.availableReplicas}{"|"}{.spec.strategy.type}{"|"}{.spec.template.spec.containers[0].image}' \
    2>/dev/null)" || return 1
  IFS='|' read -r desired updated ready available strategy image <<<"$row"
  [[ "$desired" == "1" && "$updated" == "1" && "$ready" == "1" \
     && "$available" == "1" && "$strategy" == "Recreate" \
     && "$image" == *"@sha256:${EXPECTED_INGESTOR_DIGEST}" ]] || return 1
  expected_pods_ready ingestor "$EXPECTED_INGESTOR_DIGEST" 1
}

feature_flags_safe() {
  local row capability vector active device
  row="$(k get configmap verdify-config \
    -o jsonpath='{.data.VERDIFY_COMPONENT_EXPERIMENT_ENABLED}{"|"}{.data.VERDIFY_POLICY_VECTOR_MODE}{"|"}{.data.VERDIFY_ACTIVE_EXPERIMENT_ID}{"|"}{.data.VERDIFY_DEVICE_WRITE_ENABLED}' \
    2>/dev/null)" || return 1
  IFS='|' read -r capability vector active device <<<"$row"
  [[ "$capability" == "off" && "$vector" == "off" && -z "$active" && "$device" == "1" ]]
}

public_health_safe() {
  local api mcp
  api="$(curl -fsS --max-time 10 "$API_HEALTH_URL")" || return 1
  mcp="$(curl -fsS --max-time 10 "$MCP_READY_URL")" || return 1
  jq -e '
    .status == "ok" and
    .checks.service_ingestor == "ok" and
    .checks.service_climate_action_log == "ok" and
    (.checks.climate_action_log_proof_missing == "") and
    ((.checks.climate_age_seconds | tonumber) < 180) and
    ((.checks.climate_action_log_age_seconds | tonumber) < 180)
  ' >/dev/null <<<"$api" || return 1
  jq -e '
    .ready == true and .db == "ok" and
    .auth_misconfigured == false and
    ((.missing_tools | length) == 0)
  ' >/dev/null <<<"$mcp"
}

wait_for_rollout_and_health() {
  local deadline=$(( $(date -u +%s) + ROLLOUT_TIMEOUT_SECONDS ))
  while (( $(date -u +%s) <= deadline )); do
    if deployment_converged verdify-api "$EXPECTED_API_DIGEST" \
      && deployment_converged verdify-mcp "$EXPECTED_MCP_DIGEST" \
      && deployment_converged verdify-ingestor "$EXPECTED_INGESTOR_DIGEST" \
      && deployment_converged experiment-v2-lifecycle "$EXPECTED_ORCHESTRATOR_DIGEST" \
      && deployment_converged experiment-v2-selector "$EXPECTED_ORCHESTRATOR_DIGEST" \
      && deployment_converged experiment-v2-freezer "$EXPECTED_ORCHESTRATOR_DIGEST" \
      && expected_pods_ready api "$EXPECTED_API_DIGEST" 2 \
      && expected_pods_ready mcp "$EXPECTED_MCP_DIGEST" 2 \
      && ingestor_singleton_safe \
      && expected_pods_ready experiment-v2-lifecycle "$EXPECTED_ORCHESTRATOR_DIGEST" 1 \
      && expected_pods_ready experiment-v2-selector "$EXPECTED_ORCHESTRATOR_DIGEST" 1 \
      && expected_pods_ready experiment-v2-freezer "$EXPECTED_ORCHESTRATOR_DIGEST" 1 \
      && db_stable \
      && feature_flags_safe \
      && public_health_safe
    then
      return 0
    fi
    sleep "$POLL_SECONDS"
  done
  return 1
}

snapshot_hook_baseline
BASELINE_DB_STS_UID="$(k get statefulset verdify-db -o jsonpath='{.metadata.uid}' 2>/dev/null)" \
  || die "could not capture baseline verdify-db StatefulSet UID"
BASELINE_DB_POD_UID="$(k get pod verdify-db-0 -o jsonpath='{.metadata.uid}' 2>/dev/null)" \
  || die "could not capture baseline verdify-db-0 Pod UID"
readonly BASELINE_DB_STS_UID BASELINE_DB_POD_UID
snapshot_workloads before
snapshot_feature_flags before || die "could not capture the four allowlisted verdify-config keys"
capture_public_health before || die "could not capture allowlisted public health"

if [[ "$MODE" == "dry-run" ]]; then
  absent=0
  for key in "${HOOK_KEYS[@]}"; do
    [[ -z "${BASELINE_UID[$key]}" ]] && absent=$(( absent + 1 ))
  done
  feature_gate_status=FAIL
  public_health_status=FAIL
  feature_flags_safe && feature_gate_status=PASS
  public_health_safe && public_health_status=PASS
  {
    if [[ "$feature_gate_status" == "PASS" && "$public_health_status" == "PASS" ]]; then
      printf 'status\tPASS\n'
    else
      printf 'status\tFAIL\n'
    fi
    printf 'mode\tdry-run\n'
    printf 'exact_hooks_absent\t%s/4\n' "$absent"
    printf 'feature_off_vector_off_active_id_empty\t%s\n' "$feature_gate_status"
    printf 'public_health_acceptance\t%s\n' "$public_health_status"
    printf 'cluster_mutation\tnone\n'
    printf 'secret_reads\tnone\n'
    printf 'full_configmap_reads\tnone\n'
    printf 'argocd_actions\tnone\n'
  } >"$OUT/summary.tsv"
  [[ "$feature_gate_status" == "PASS" && "$public_health_status" == "PASS" ]] \
    || die "dry-run reads succeeded but current feature/health acceptance is not safe"
  printf 'DRY-RUN PASS: %s/4 exact hooks absent; read-only snapshots in %s\n' \
    "$absent" "$OUT"
  exit 0
fi

declare -A WATCH_PID=()
for key in "${HOOK_KEYS[@]}"; do
  capture_hook "$key" &
  WATCH_PID[$key]="$!"
done

hook_failures=0
for key in "${HOOK_KEYS[@]}"; do
  if ! wait "${WATCH_PID[$key]}"; then
    hook_failures=$(( hook_failures + 1 ))
  fi
done

if (( hook_failures != 0 )); then
  snapshot_workloads failed
  snapshot_feature_flags failed || true
  capture_public_health failed || true
  {
    printf 'status\tFAIL\n'
    printf 'release\t%s\n' "$RELEASE"
    printf 'hook_failures\t%s\n' "$hook_failures"
    printf 'workload_rollout\tnot accepted\n'
  } >"$OUT/summary.tsv"
  die "$hook_failures hook evidence watcher(s) failed; inspect result/assertion files (raw logs are never printed)"
fi

printf '[%s] all four PreSync hook receipts captured; waiting for exact workload convergence\n' "$(now_utc)"
if ! wait_for_rollout_and_health; then
  snapshot_workloads failed-rollout
  snapshot_feature_flags failed-rollout || true
  capture_public_health failed-rollout || true
  {
    printf 'status\tFAIL\n'
    printf 'release\t%s\n' "$RELEASE"
    printf 'hooks\tPASS\n'
    printf 'workload_rollout\tFAIL\n'
    printf 'single_writer\tFAIL-or-unproven\n'
  } >"$OUT/summary.tsv"
  die "hooks passed but exact workload/health convergence was not observed before timeout"
fi

snapshot_workloads after
snapshot_feature_flags after
capture_public_health after

{
  printf 'status\tPASS\n'
  printf 'release\t%s\n' "$RELEASE"
  printf 'hooks\t4/4 Complete=True with receipt and image assertions\n'
  printf 'api\t2 Ready pods on exact expected digest; public health ok\n'
  printf 'mcp\t2 Ready pods on exact expected digest; readyz DB ok\n'
  printf 'ingestor\texactly 1 Ready zero-restart pod on expected digest; Recreate deployment; fresh public ingestor/climate evidence\n'
  printf 'experiment_workers\t3/3 Ready zero-restart pods on expected orchestrator digest\n'
  printf 'experiment_capability\toff\n'
  printf 'policy_vector_mode\toff\n'
  printf 'active_experiment_id\tempty\n'
  printf 'device_write_enabled\t1 (unchanged existing production writer)\n'
  printf 'lease_evidence\tnot captured: repo service account lacks get on coordination.k8s.io Lease\n'
  printf 'finished_at\t%s\n' "$(now_utc)"
} >"$OUT/summary.tsv"

printf 'CAPTURE PASS: exact hook, rollout, public health, and single-writer evidence in %s\n' "$OUT"
