#!/usr/bin/env bash
# k3s-smoke.sh — post-deploy smoke + device-route safety monitor for the
# Verdify k3s instances (#89, G10).
#
# WHAT THIS IS
#   A READ-ONLY, idempotent post-deploy verifier for the k3s Verdify app
#   instance. Run it AFTER ArgoCD reports the app green, to assert that what is
#   actually running matches what we deployed and that the prod service surfaces
#   are healthy. It NEVER mutates the cluster, NEVER
#   touches the live VM / ESP32 / docker-compose, and NEVER scales anything.
#
#   Two modes:
#     smoke   (default)  — full post-green smoke of an instance (default ns
#                          verdify-prod). Asserts:
#                            1. api source SHA and desired/running digest match
#                               the explicit, reviewed release receipt.
#                            2. every MCP replica authenticates and returns the
#                               exact Iris tool inventory without exporting a token.
#                            3. database, climate/action data, and setpoint
#                               readbacks are fresh.
#                            4. In prod, use device-monitor for the separate
#                               single-writer socket invariant.
#
#     device-monitor      — prod exactly-one-writer monitor. Counts the
#                          ESTABLISHED TCP sockets in the target namespace
#                          (default verdify-prod) connected to
#                          192.168.10.111:6053 (the live ESP32
#                          ESPHome native API) and asserts the count is exactly
#                          one. An unreadable pod makes the result unknown.
#
# SAFETY (hard rules this script obeys — see AGENTS.md + k3s-cutover-sequence.md)
#   - READ-ONLY against the cluster. Only `kubectl get/exec`(read commands) and
#     a localhost `kubectl port-forward` (which mutates nothing server-side).
#   - Never scales, patches, applies, deletes, or syncs anything.
#   - Never writes the argocd namespace and never triggers an ArgoCD sync.
#   - Never touches the live VM, docker-compose, the ESP32, or pushes a setpoint.
#   - The socket checks read each pod's own /proc/net/tcp via `kubectl exec`
#     (falling back to ss/netstat); they open no new device connection.
#
# USAGE
#   KUBECONFIG=/home/jason/.kube/verdify-agent.config \
#     scripts/k3s-smoke.sh smoke --expected-api-sha SHA --expected-api-digest sha256:HEX [--namespace NS]
#     scripts/k3s-smoke.sh device-monitor [--namespace NS]
#
#   Environment / flags:
#     KUBECONFIG            (required) path to the scoped kubeconfig.
#     --namespace NS        target namespace (default: verdify-prod).
#     --mode MODE           same as the positional MODE arg.
#     --api-port PORT       localhost port for the api port-forward (default 18080).
#     --timeout SECS        per-check curl/exec timeout (default 10).
#     --expected-api-sha SHA     40-hex source commit of the built API image.
#     --expected-api-digest sha256:HEX  API digest from the reviewed Git pin.
#     -h | --help
#
#   Exit code 0 = all checks passed. Non-zero = at least one check failed (the
#   summary lists exactly which). The script is idempotent: re-running it has no
#   side effects and yields the same verdict for the same cluster state.
#
# DO NOT run this against the cluster during an in-progress cutover unless the
# instance is reported green; it is a post-green verifier, not a liveness poke.

set -uo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
MODE="smoke"
NAMESPACE=""
API_PORT="18080"
TIMEOUT="10"
EXPECTED_API_SHA=""
EXPECTED_API_DIGEST=""
DEVICE_VLAN_CIDR="192.168.10.0/24"
DEVICE_ESP32_IP="192.168.10.111"
DEVICE_PORT="6053"

# ── Arg parse ────────────────────────────────────────────────────────────────
while [ $# -gt 0 ]; do
  case "$1" in
    smoke|device-monitor) MODE="$1"; shift ;;
    --mode) MODE="${2:-}"; shift 2 ;;
    --namespace|-n) NAMESPACE="${2:-}"; shift 2 ;;
    --api-port) API_PORT="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    --expected-api-sha) EXPECTED_API_SHA="${2:-}"; shift 2 ;;
    --expected-api-digest) EXPECTED_API_DIGEST="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,80p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "${NAMESPACE}" ]; then
  NAMESPACE="verdify-prod"
fi

if [ -z "${KUBECONFIG:-}" ]; then
  echo "ERROR: KUBECONFIG must be set (the scoped verdify-agent kubeconfig)." >&2
  exit 2
fi
if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl not found on PATH." >&2
  exit 2
fi
if [ "${MODE}" = "smoke" ]; then
  if ! [[ "${EXPECTED_API_SHA}" =~ ^[0-9a-f]{40}$ ]] ||
     ! [[ "${EXPECTED_API_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "ERROR: smoke requires --expected-api-sha (40 hex) and --expected-api-digest (sha256:64 hex) from the reviewed release receipt." >&2
    exit 2
  fi
fi

KC=(kubectl --kubeconfig "${KUBECONFIG}" -n "${NAMESPACE}")

PASS=0; FAIL=0
declare -a RESULTS=()

pass() { echo "  PASS  $1"; RESULTS+=("PASS  $1"); PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1"; RESULTS+=("FAIL  $1"); FAIL=$((FAIL+1)); }
info() { echo "  ..    $1"; }

# Background pids we are responsible for cleaning up (only our own
# `kubectl port-forward` processes — never any cluster resource).
declare -a PF_PIDS=()
cleanup() {
  local pid
  for pid in "${PF_PIDS[@]:-}"; do
    [ -n "${pid}" ] && kill "${pid}" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

# ── Helpers ──────────────────────────────────────────────────────────────────

# Start a localhost port-forward to a Service; record pid for cleanup. Returns
# 0 if the forward came up within TIMEOUT, else 1. port-forward mutates nothing
# server-side — it is a local tunnel.
start_port_forward() {
  local svc="$1" lport="$2" rport="$3" pid waited
  ( "${KC[@]}" port-forward "svc/${svc}" "${lport}:${rport}" >/dev/null 2>&1 ) &
  pid=$!
  PF_PIDS+=("${pid}")
  waited=0
  while [ "${waited}" -lt "${TIMEOUT}" ]; do
    if ! kill -0 "${pid}" >/dev/null 2>&1; then return 1; fi
    if (exec 3<>"/dev/tcp/127.0.0.1/${lport}") 2>/dev/null; then exec 3>&- 3<&-; return 0; fi
    waited=$((waited+1)); sleep 1
  done
  return 1
}

# ── Mode: smoke ──────────────────────────────────────────────────────────────
run_smoke() {
  echo "=== k3s smoke ($(date '+%Y-%m-%d %H:%M:%S')) — namespace=${NAMESPACE} ==="
  echo "(READ-ONLY: no scale/patch/apply/sync; no device touch.)"
  echo ""

  # 1. Bind the reviewed source and digest to the Deployment and every running
  #    API container. Digest-only Kubernetes images carry no source SHA tag.
  echo "[1] api /health/detailed — image provenance"
  local img
  img="$("${KC[@]}" get deploy verdify-api \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}' 2>/dev/null || true)"
  if [ -z "${img}" ]; then
    fail "api: could not read deployed image off Deployment verdify-api"
  else
    info "deployed api image: ${img}"
    if [[ "${img}" == *@"${EXPECTED_API_DIGEST}" ]]; then
      pass "api: Deployment digest matches reviewed ${EXPECTED_API_DIGEST}"
    else
      fail "api: Deployment digest differs from reviewed ${EXPECTED_API_DIGEST}"
    fi
    local pods_json image_verdict
    pods_json="$("${KC[@]}" get pods -l app.kubernetes.io/component=api -o json 2>/dev/null || true)"
    image_verdict="$(printf '%s' "${pods_json}" | python3 -c '
import json, sys
try:
    pods = json.load(sys.stdin)["items"]
    expected = sys.argv[1]
    assert pods, "no API pods"
    for pod in pods:
        name = pod["metadata"]["name"]
        assert pod["status"]["phase"] == "Running", f"{name} not Running"
        matches = [s for s in pod["status"].get("containerStatuses", []) if s["name"] == "api"]
        assert len(matches) == 1 and matches[0]["ready"], f"{name} API container unready"
        spec_matches = [c for c in pod["spec"]["containers"] if c["name"] == "api"]
        assert len(spec_matches) == 1 and spec_matches[0]["image"] == expected, f"{name} spec image differs"
        assert matches[0]["imageID"].split("@")[-1] == expected.split("@")[-1], f"{name} running imageID differs"
    print(f"{len(pods)} ready API pod(s) run Deployment digest")
except (ValueError, KeyError, AssertionError, TypeError) as exc:
    print(f"running image identity unavailable/mismatch: {exc}")
    sys.exit(1)
' "${img}" 2>/dev/null)"
    if [ "$?" -eq 0 ]; then pass "api: ${image_verdict}"; else fail "api: ${image_verdict}"; fi
    if start_port_forward verdify-api "${API_PORT}" 8080; then
      local body git_sha db_ok health_body health_verdict
      body="$(curl -fsS --max-time "${TIMEOUT}" "http://127.0.0.1:${API_PORT}/health/detailed" 2>/dev/null || true)"
      if [ -z "${body}" ]; then
        fail "api: /health/detailed unreachable / empty response"
      else
        git_sha="$(printf '%s' "${body}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("git_sha", ""))' 2>/dev/null || true)"
        db_ok="$(printf '%s' "${body}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("checks", {}).get("db_reachable", False))' 2>/dev/null || true)"
        info "/health/detailed git_sha=${git_sha:-<none>}"
        if [ "${git_sha}" = "${EXPECTED_API_SHA}" ]; then
          pass "api: baked git_sha matches reviewed source ${EXPECTED_API_SHA}"
        else
          fail "api: baked git_sha differs from reviewed source ${EXPECTED_API_SHA}"
        fi
        if [ "${db_ok}" = "True" ]; then
          pass "db: reachable (api /health/detailed checks.db_reachable=true)"
        else
          fail "db: NOT reachable (api /health/detailed checks.db_reachable!=true)"
        fi
        health_body="$(curl -fsS --max-time "${TIMEOUT}" "http://127.0.0.1:${API_PORT}/health" 2>/dev/null || true)"
        health_verdict="$(printf '%s' "${health_body}" | python3 -c '
import json, sys
try:
    body = json.load(sys.stdin)
    checks = body["checks"]
    assert body["status"] == "ok", "API data status is degraded"
    for key in ("climate_age_seconds", "climate_action_log_age_seconds"):
        age = checks[key]
        assert isinstance(age, (int, float)) and 0 <= age <= 300, f"{key} stale/missing"
    assert checks["climate_action_log_proof_missing"] == "", "controller action proof missing"
    print("climate and controller action proof fresh (<=300s)")
except (ValueError, KeyError, AssertionError, TypeError) as exc:
    print(f"data freshness unavailable/degraded: {exc}")
    sys.exit(1)
' 2>/dev/null)"
        if [ "$?" -eq 0 ]; then pass "api: ${health_verdict}"; else fail "api: ${health_verdict}"; fi
      fi
    else
      fail "api: port-forward to svc/verdify-api:8080 did not come up within ${TIMEOUT}s"
    fi
  fi
  echo ""

  # 2. Authenticate from inside each MCP pod. The bearer stays in the pod env;
  #    an HTTP 401 or empty inventory never passes this smoke.
  echo "[2] mcp — streamable-http /mcp tool surface"
  local expected_tools mcp_pods pod mcp_verdict mcp_desired mcp_ready mcp_count
  expected_tools="$(python3 -c 'import sys,yaml; p=yaml.safe_load(open(sys.argv[1])); c=yaml.safe_load(p["data"]["config.yaml"]); print(",".join(sorted(c["mcp_servers"]["verdify_greenhouse"]["tools"]["include"])))' \
    "$(dirname "$0")/../deploy/k8s/components/hermes-iris/hermes-config.yaml" 2>/dev/null || true)"
  mcp_pods="$("${KC[@]}" get pods -l app.kubernetes.io/component=mcp --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)"
  mcp_desired="$("${KC[@]}" get deploy verdify-mcp -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
  mcp_ready="$("${KC[@]}" get deploy verdify-mcp -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  mcp_count="$(printf '%s\n' "${mcp_pods}" | awk 'NF { n++ } END { print n+0 }')"
  if [[ "${mcp_desired}" =~ ^[0-9]+$ ]] && [ "${mcp_desired}" -gt 0 ] &&
     [ "${mcp_desired}" = "${mcp_ready}" ] && [ "${mcp_desired}" = "${mcp_count}" ]; then
    pass "mcp: ${mcp_count}/${mcp_desired} replicas Running and Ready"
  else
    fail "mcp: desired=${mcp_desired:-?} ready=${mcp_ready:-?} Running=${mcp_count}"
  fi
  if [ -z "${expected_tools}" ] || [ -z "${mcp_pods}" ]; then
    fail "mcp: canonical Iris inventory or Running pods unavailable"
  else
    while IFS= read -r pod; do
      [ -z "${pod}" ] && continue
      mcp_verdict="$("${KC[@]}" exec -i "${pod}" -c mcp -- python - "${expected_tools}" "${TIMEOUT}" <<'PY' 2>/dev/null
import json, os, sys, urllib.error, urllib.request

def request(method, ident, token):
    payload = {"jsonrpc": "2.0", "id": ident, "method": method, "params": {}}
    if method == "initialize":
        payload["params"] = {"protocolVersion": "2025-11-25", "capabilities": {},
                             "clientInfo": {"name": "verdify-g10-smoke", "version": "1"}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json",
               "authorization": "Bearer " + token}
    if method != "initialize":
        headers["mcp-protocol-version"] = "2025-11-25"
    req = urllib.request.Request("http://127.0.0.1:8000/mcp", json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=float(sys.argv[2])) as response:
        if response.status != 200:
            raise ValueError("protocol HTTP status was not 200")
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError("protocol response too large")
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            raw = next(line[6:] for line in raw.splitlines() if line.startswith(b"data: "))
        return json.loads(raw)

try:
    token = os.environ["VERDIFY_MCP_TOKEN_IRIS"]
    assert token, "Iris bearer absent"
    assert "result" in request("initialize", 1, token), "initialize result absent"
    actual = {tool["name"] for tool in request("tools/list", 2, token)["result"]["tools"]}
    expected = set(sys.argv[1].split(","))
    assert actual == expected, "Iris inventory differs from source"
    print(f"authenticated initialize and exact {len(actual)}-tool Iris inventory")
except (OSError, KeyError, StopIteration, ValueError, AssertionError, TypeError) as exc:
    print(f"authenticated MCP tool-list failed: {type(exc).__name__}: {exc}")
    sys.exit(1)
PY
)"
      if [ "$?" -eq 0 ]; then pass "mcp pod ${pod}: ${mcp_verdict}"; else fail "mcp pod ${pod}: ${mcp_verdict:-tool-list unavailable}"; fi
    done <<< "${mcp_pods}"
  fi
  echo ""

  # 3. Setpoint readbacks must keep advancing. This is observation, not a
  #    synthetic setpoint write or confirmation claim for a particular plan.
  echo "[3] setpoint observation — fresh snapshot"
  local snapshot fields snapshot_age snapshot_count
  fields="$(PYTHONPATH="$(dirname "$0")/.." python3 -c '
from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER
assert len(CANONICAL_FIELD_ORDER) == 48
assert all(name.replace("_", "").isalnum() for name in CANONICAL_FIELD_ORDER)
print("ARRAY[" + ",".join("\x27" + name + "\x27" for name in CANONICAL_FIELD_ORDER) + "]::text[]")
' 2>/dev/null || true)"
  if [ -n "${fields}" ]; then
    snapshot="$("${KC[@]}" exec verdify-db-0 -c postgres -- psql -U verdify -d verdify -At \
      -c "WITH complete AS (SELECT ts FROM setpoint_snapshot WHERE greenhouse_id='vallery' AND zone IS NULL AND band_role IS NULL AND parameter=ANY(${fields}) AND ts>now()-interval '5 minutes' GROUP BY ts HAVING count(DISTINCT parameter)=48 ORDER BY ts DESC LIMIT 1) SELECT coalesce(extract(epoch from now()-max(ts))::int,999999),count(*) FROM complete" \
      2>/dev/null || true)"
  else
    snapshot=""
  fi
  IFS='|' read -r snapshot_age snapshot_count <<< "${snapshot}"
  if [[ "${snapshot_age}" =~ ^[0-9]+$ ]] && [ "${snapshot_count}" = "1" ] &&
     [ "${snapshot_age}" -le 300 ]; then
    pass "setpoint snapshot fresh (${snapshot_age}s; complete canonical 48-field batch)"
  else
    fail "setpoint snapshot stale/incomplete/unavailable (age=${snapshot_age:-?}s; complete batches=${snapshot_count:-?})"
  fi
  info "run device-monitor for the separate production single-writer socket invariant"
  echo ""

  print_summary
}

# ── Mode: device-monitor (prod exactly-one-writer) ──────────────────────────
pod_departed() {
  local pod="$1" expected_uid="$2" state uid phase restart_policy containers terminations ended
  if ! state="$("${KC[@]}" get pod "${pod}" --ignore-not-found \
    -o jsonpath='{.metadata.uid}{"|"}{.status.phase}{"|"}{.spec.restartPolicy}{"|"}{range .spec.containers[*]}x{end}{range .spec.ephemeralContainers[*]}x{end}{"|"}{range .status.containerStatuses[*]}{.state.terminated.finishedAt}{";"}{end}{range .status.ephemeralContainerStatuses[*]}{.state.terminated.finishedAt}{";"}{end}' \
    2>/dev/null)"; then
    return 1
  fi
  IFS='|' read -r uid phase restart_policy containers terminations <<< "${state}"
  ended="${terminations//[!;]/}"
  [ -z "${uid}" ] || [ "${uid}" != "${expected_uid}" ] || \
    [ "${phase}" = "Succeeded" ] || [ "${phase}" = "Failed" ] || \
    { [ "${phase}" = "Running" ] && [ "${restart_policy}" = "Never" ] && [ -n "${containers}" ] && \
      [ "${#containers}" -eq "${#ended}" ] && [[ "${terminations}" =~ ^([^\;]+\;)+$ ]]; }
}

run_device_monitor() {
  echo "=== k3s device-route monitor ($(date '+%Y-%m-%d %H:%M:%S')) — namespace=${NAMESPACE} ==="
  echo "(READ-ONLY: inspects pods' own sockets; asserts EXACTLY ONE writer to ${DEVICE_ESP32_IP}:${DEVICE_PORT}.)"
  echo ""
  local pods final_pods pod uid sockets=0 unknown=0 out count containers container observed_uid
  local socket_holders=""
  local octet1 octet2 octet3 octet4 remote_hex
  IFS=. read -r octet1 octet2 octet3 octet4 <<< "${DEVICE_ESP32_IP}"
  remote_hex="$(printf '%02X%02X%02X%02X:%04X' "${octet4}" "${octet3}" "${octet2}" "${octet1}" "${DEVICE_PORT}")"
  if ! pods="$("${KC[@]}" get pods --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.uid}{"\n"}{end}' 2>/dev/null)"; then
    fail "device-monitor: Running pod inventory unavailable (writer count UNKNOWN)"
    print_summary; return
  fi
  if [ -z "${pods}" ]; then
    fail "device-monitor: no Running pods in ${NAMESPACE} — cannot observe the single writer"
    print_summary; return
  fi
  while read -r pod uid; do
    [ -z "${pod}" ] && continue
    out="$("${KC[@]}" exec "${pod}" -- cat /proc/net/tcp 2>/dev/null)" || out=""
    if [ -z "${out}" ]; then
      # A Running pod may have a completed default container and a live
      # sidecar/ephemeral container. They share the pod network namespace.
      # Bind the fallback to the inventoried UID; a replacement stays UNKNOWN.
      containers="$("${KC[@]}" get pod "${pod}" -o jsonpath='{.metadata.uid}{"\n"}{range .status.containerStatuses[?(@.state.running)]}{.name}{"\n"}{end}{range .status.ephemeralContainerStatuses[?(@.state.running)]}{.name}{"\n"}{end}' 2>/dev/null)" || containers=""
      observed_uid="${containers%%$'\n'*}"
      if [ "${observed_uid}" = "${uid}" ]; then
        while read -r container; do
          [ -z "${container}" ] && continue
          if out="$("${KC[@]}" exec "${pod}" -c "${container}" -- cat /proc/net/tcp 2>/dev/null)" && [ -n "${out}" ]; then
            break
          fi
          out=""
        done <<< "${containers#*$'\n'}"
      fi
    fi
    if [ -n "${out}" ]; then
      count="$(awk -v remote="${remote_hex}" 'NR > 1 && $3 == remote && $4 == "01" { n++ } END { print n+0 }' <<< "${out}")"
    else
      # Images without cat may still include a socket tool. A failed exec is
      # UNKNOWN, never proof that the pod has zero device sockets.
      out="$("${KC[@]}" exec "${pod}" -- sh -c \
        'command -v ss >/dev/null 2>&1 && ss -tn 2>/dev/null || (command -v netstat >/dev/null 2>&1 && netstat -tn 2>/dev/null)' \
        2>/dev/null)" || out=""
      if [ -z "${out}" ]; then
        # A short-lived Job may finish after the Running snapshot. Only a
        # confirmed departed pod is safe to omit; a live unreadable pod is
        # still an unknown possible device writer.
        if pod_departed "${pod}" "${uid}"; then
          info "pod ${pod}: departed before socket read"
          continue
        fi
        unknown=$((unknown+1))
        info "pod ${pod}: socket table unavailable (UNKNOWN)"
        continue
      fi
      count="$(awk -v remote="${DEVICE_ESP32_IP}:${DEVICE_PORT}" \
        '($1 == "ESTAB" || $6 == "ESTABLISHED") && index($0, remote) { n++ } END { print n+0 }' <<< "${out}")"
    fi
    sockets=$((sockets+count))
    if [ "${count}" -gt 0 ]; then
      socket_holders+="${pod} ${uid}"$'\n'
    fi
    info "pod ${pod}: ${count} established ESP32 socket(s)"
  done <<< "${pods}"

  if ! final_pods="$("${KC[@]}" get pods --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.metadata.uid}{"\n"}{end}' 2>/dev/null)"; then
    unknown=$((unknown+1))
    info "final Running pod inventory unavailable (UNKNOWN)"
  else
    while read -r pod uid; do
      [ -z "${pod}" ] && continue
      if ! grep -Fxq "${pod} ${uid}" <<< "${pods}"; then
        if pod_departed "${pod}" "${uid}"; then
          info "pod ${pod}: appeared and departed during socket scan"
        else
          unknown=$((unknown+1))
          info "pod ${pod}: appeared after socket scan (UNKNOWN)"
        fi
      fi
    done <<< "${final_pods}"
    while read -r pod uid; do
      [ -z "${pod}" ] && continue
      if ! grep -Fxq "${pod} ${uid}" <<< "${final_pods}"; then
        unknown=$((unknown+1))
        info "pod ${pod}: observed socket holder no longer Running (UNKNOWN)"
      fi
    done <<< "${socket_holders}"
  fi

  if [ "${unknown}" -gt 0 ]; then
    fail "device-monitor: ${unknown} pod(s) unobservable; ${sockets} socket(s) observed (writer count UNKNOWN)"
  elif [ "${sockets}" -eq 1 ]; then
    pass "device-monitor: EXACTLY ONE ESP32 writer connection observed"
  elif [ "${sockets}" -eq 0 ]; then
    fail "device-monitor: ZERO ESP32 writer connections observed (device loop down?)"
  else
    fail "device-monitor: ${sockets} ESP32 writer connections observed — MULTI-WRITER risk"
  fi
  echo ""
  print_summary
}

print_summary() {
  echo "── summary (${NAMESPACE}) ─────────────────────────────────"
  echo "  passed=${PASS} failed=${FAIL}"
  if [ "${FAIL}" -gt 0 ]; then
    echo "  RESULT: NOT GREEN — review the FAIL lines above."
    return 1
  fi
  echo "  RESULT: GREEN"
  return 0
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "${MODE}" in
  smoke)          run_smoke ;;
  device-monitor) run_device_monitor ;;
  *) echo "ERROR: unknown mode '${MODE}' (expected: smoke | device-monitor)" >&2; exit 2 ;;
esac
exit $?
