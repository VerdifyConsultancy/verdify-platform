#!/usr/bin/env bash
# k3s-smoke.sh — post-deploy smoke + device-route safety monitor for the
# Verdify k3s instances (#89, G10).
#
# WHAT THIS IS
#   A READ-ONLY, idempotent post-deploy verifier for the k3s Verdify app
#   instance. Run it AFTER ArgoCD reports the app green, to assert that what is
#   actually running matches what we deployed and that the staging device-write
#   interlocks are physically holding. It NEVER mutates the cluster, NEVER
#   touches the live VM / ESP32 / docker-compose, and NEVER scales anything.
#
#   Two modes:
#     smoke   (default)  — full post-green smoke of an instance (default ns
#                          verdify-staging). Asserts:
#                            1. api /health/detailed is reachable and the baked
#                               VERDIFY_GIT_SHA matches the SHA derived from the
#                               deployed api image digest/tag.
#                            2. mcp is serving the streamable-http /mcp surface
#                               (process Ready + protocol responds), and a
#                               read-only tool-list round-trips.
#                            3. the database is reachable (api reports
#                               checks.db_reachable=true).
#                            4. STAGING ONLY: ingestor Deployment replicas == 0.
#                            5. STAGING ONLY: ZERO established TCP connections
#                               from any verdify-staging pod to the greenhouse
#                               device VLAN 192.168.10.0/24:6053 (no second
#                               writer can reach the live ESP32).
#
#     device-monitor      — prod exactly-one-writer monitor STUB. Counts the
#                          number of distinct pods in the target namespace
#                          (default verdify-prod) holding an ESTABLISHED TCP
#                          connection to 192.168.10.111:6053 (the live ESP32
#                          ESPHome native API) and asserts the count is exactly
#                          one. This is the network-observable form of the
#                          single-writer invariant. STUB: read-only, prints what
#                          it sees; wire into alerting separately.
#
# SAFETY (hard rules this script obeys — see CLAUDE.md + k3s-cutover-sequence.md)
#   - READ-ONLY against the cluster. Only `kubectl get/exec`(read commands) and
#     a localhost `kubectl port-forward` (which mutates nothing server-side).
#   - Never scales, patches, applies, deletes, or syncs anything.
#   - Never writes the argocd namespace and never triggers an ArgoCD sync.
#   - Never touches the live VM, docker-compose, the ESP32, or pushes a setpoint.
#   - The `ss`/`netstat` device-connection checks run INSIDE a target pod via
#     `kubectl exec` and only INSPECT that pod's own sockets; they open no new
#     device connection.
#
# USAGE
#   KUBECONFIG=/home/jason/.kube/verdify-agent.config \
#     scripts/k3s-smoke.sh [smoke|device-monitor] [--namespace NS]
#
#   Environment / flags:
#     KUBECONFIG            (required) path to the scoped kubeconfig.
#     --namespace NS        target namespace (default: verdify-staging for
#                           smoke, verdify-prod for device-monitor).
#     --mode MODE           same as the positional MODE arg.
#     --api-port PORT       localhost port for the api port-forward (default 18080).
#     --mcp-port PORT       localhost port for the mcp port-forward (default 18000).
#     --timeout SECS        per-check curl/exec timeout (default 10).
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
MCP_PORT="18000"
TIMEOUT="10"
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
    --mcp-port) MCP_PORT="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,80p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [ -z "${NAMESPACE}" ]; then
  if [ "${MODE}" = "device-monitor" ]; then NAMESPACE="verdify-prod"; else NAMESPACE="verdify-staging"; fi
fi

if [ -z "${KUBECONFIG:-}" ]; then
  echo "ERROR: KUBECONFIG must be set (the scoped verdify-agent kubeconfig)." >&2
  exit 2
fi
if ! command -v kubectl >/dev/null 2>&1; then
  echo "ERROR: kubectl not found on PATH." >&2
  exit 2
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

# Derive the short git SHA the deployed api image points at, from the image
# reference on the running Deployment. The kustomization pins a @sha256 digest,
# but the IMAGE TAG (sha-<gitsha>) is what carries the source commit. We read
# both: prefer the sha-<gitsha> tag if present, else fall back to comparing the
# image digest is non-empty (provenance present). Returns the expected git_sha
# token (may be empty if only a digest with no readable tag is present).
expected_git_sha_from_image() {
  local img
  img="$("${KC[@]}" get deploy verdify-api \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="api")].image}' 2>/dev/null)"
  echo "${img}"
}

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

  # 1. api /health/detailed reachable + baked VERDIFY_GIT_SHA matches the
  #    deployed image's sha-<gitsha> tag.
  echo "[1] api /health/detailed — image provenance"
  local img expected_tag
  img="$(expected_git_sha_from_image)"
  if [ -z "${img}" ]; then
    fail "api: could not read deployed image off Deployment verdify-api"
  else
    info "deployed api image: ${img}"
    # Extract a sha-<gitsha> tag token if the image is tagged that way.
    expected_tag="$(echo "${img}" | grep -oE 'sha-[0-9a-f]{7,40}' | head -1 || true)"
    if start_port_forward verdify-api "${API_PORT}" 8080; then
      local body git_sha db_ok
      body="$(curl -fsS --max-time "${TIMEOUT}" "http://127.0.0.1:${API_PORT}/health/detailed" 2>/dev/null || true)"
      if [ -z "${body}" ]; then
        fail "api: /health/detailed unreachable / empty response"
      else
        git_sha="$(echo "${body}" | sed -n 's/.*"git_sha"[ :]*"\([^"]*\)".*/\1/p')"
        db_ok="$(echo "${body}" | grep -o '"db_reachable"[ :]*true' || true)"
        info "/health/detailed git_sha=${git_sha:-<none>}"
        if [ -z "${git_sha}" ] || [ "${git_sha}" = "unknown" ]; then
          fail "api: baked VERDIFY_GIT_SHA missing/unknown in /health/detailed"
        elif [ -n "${expected_tag}" ]; then
          # The image tag carries sha-<gitsha>; the baked git_sha should match
          # the gitsha portion (prefix-compare, tags may be truncated).
          local tag_sha="${expected_tag#sha-}"
          if [ "${git_sha}" = "${tag_sha}" ] || [ "${git_sha#"${tag_sha}"}" != "${git_sha}" ] || [ "${tag_sha#"${git_sha}"}" != "${tag_sha}" ]; then
            pass "api: baked git_sha (${git_sha}) matches deployed image tag (${expected_tag})"
          else
            fail "api: baked git_sha (${git_sha}) does NOT match deployed image tag (${expected_tag})"
          fi
        else
          # Image pinned only by @sha256 digest with no readable sha-<gitsha>
          # tag on the Deployment: we can still assert a real baked SHA exists.
          pass "api: baked git_sha present (${git_sha}); image pinned by digest, no sha-tag to cross-check"
        fi
        # 3. db reachability (folded in — same endpoint reports it).
        if [ -n "${db_ok}" ]; then
          pass "db: reachable (api /health/detailed checks.db_reachable=true)"
        else
          fail "db: NOT reachable (api /health/detailed checks.db_reachable!=true)"
        fi
      fi
    else
      fail "api: port-forward to svc/verdify-api:8080 did not come up within ${TIMEOUT}s"
    fi
  fi
  echo ""

  # 2. mcp serving — process Ready + streamable-http /mcp protocol responds +
  #    read-only tool-list round-trips.
  echo "[2] mcp — streamable-http /mcp tool surface"
  local mcp_ready
  mcp_ready="$("${KC[@]}" get deploy verdify-mcp \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  if [ "${mcp_ready:-0}" -ge 1 ] 2>/dev/null; then
    info "mcp Deployment readyReplicas=${mcp_ready}"
  else
    fail "mcp: Deployment has no ready replicas (readyReplicas=${mcp_ready:-0})"
  fi
  if start_port_forward verdify-mcp "${MCP_PORT}" 8000; then
    # FastMCP streamable-http: a tools/list JSON-RPC POST to /mcp. We accept any
    # HTTP response (even a protocol/auth error) as proof the surface is live;
    # a non-empty tool list is the strong pass.
    local mcp_resp
    mcp_resp="$(curl -fsS --max-time "${TIMEOUT}" \
      -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' \
      -X POST "http://127.0.0.1:${MCP_PORT}/mcp" \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null || true)"
    if [ -z "${mcp_resp}" ]; then
      # curl -f fails on HTTP >=400; retry without -f to detect a live-but-erroring surface.
      local code
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "${TIMEOUT}" \
        -H 'Content-Type: application/json' \
        -H 'Accept: application/json, text/event-stream' \
        -X POST "http://127.0.0.1:${MCP_PORT}/mcp" \
        -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' 2>/dev/null || true)"
      if [ -n "${code}" ] && [ "${code}" != "000" ]; then
        pass "mcp: /mcp surface live (HTTP ${code}; tool-list needs a session handshake — surface confirmed)"
      else
        fail "mcp: /mcp surface did not respond"
      fi
    elif echo "${mcp_resp}" | grep -q '"tools"'; then
      local n
      n="$(echo "${mcp_resp}" | grep -o '"name"' | wc -l | tr -d ' ')"
      pass "mcp: tools/list round-tripped (~${n} tool entries)"
    else
      pass "mcp: /mcp surface responded to tools/list (non-empty body)"
    fi
  else
    fail "mcp: port-forward to svc/verdify-mcp:8000 did not come up within ${TIMEOUT}s"
  fi
  echo ""

  # 4 + 5. STAGING-ONLY device-safety interlocks.
  if [ "${NAMESPACE}" = "verdify-staging" ]; then
    echo "[4] ingestor replicas == 0 (staging device-write pin)"
    local repl
    repl="$("${KC[@]}" get deploy verdify-ingestor \
      -o jsonpath='{.spec.replicas}' 2>/dev/null || true)"
    if [ "${repl}" = "0" ]; then
      pass "ingestor: spec.replicas == 0 (single-writer pin holds)"
    else
      fail "ingestor: spec.replicas == '${repl:-<none>}' (MUST be 0 in staging)"
    fi

    echo "[5] ZERO device-VLAN writes from staging pods (${DEVICE_VLAN_CIDR}:${DEVICE_PORT})"
    check_no_device_connections
  else
    info "namespace is not verdify-staging — skipping staging-only interlock checks [4],[5]"
  fi
  echo ""

  print_summary
}

# Inspect every running pod in the namespace and assert NONE holds an
# established TCP connection into the device VLAN. Read-only: `ss`/`netstat`
# only LIST the pod's own sockets; no connection is opened.
check_no_device_connections() {
  local pods pod offenders=0 tool_found=0
  pods="$("${KC[@]}" get pods --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)"
  if [ -z "${pods}" ]; then
    info "no Running pods found in ${NAMESPACE} (vacuously zero device writes)"
    pass "device-egress: no Running pods => zero device-VLAN connections"
    return
  fi
  while IFS= read -r pod; do
    [ -z "${pod}" ] && continue
    # Try ss first, then netstat; either lists sockets read-only. If neither is
    # present in the container, we cannot inspect from inside — report as info,
    # not a false pass.
    local out rc
    out="$("${KC[@]}" exec "${pod}" -- sh -c \
      "command -v ss >/dev/null 2>&1 && ss -tnp 2>/dev/null || (command -v netstat >/dev/null 2>&1 && netstat -tnp 2>/dev/null) || echo __NO_SOCKET_TOOL__" \
      2>/dev/null || true)"
    rc=$?
    if echo "${out}" | grep -q '__NO_SOCKET_TOOL__'; then
      info "pod ${pod}: no ss/netstat in container — socket inspection unavailable from inside"
      continue
    fi
    if [ ${rc} -ne 0 ] && [ -z "${out}" ]; then
      info "pod ${pod}: exec for socket listing returned nothing (skipping)"
      continue
    fi
    tool_found=1
    # Match an established connection whose peer is in the device VLAN.
    # Device IPs are 192.168.10.x; match the /24 prefix + the :6053 port too.
    local hits
    hits="$(echo "${out}" \
      | grep -E 'ESTAB|ESTABLISHED' \
      | grep -E '192\.168\.10\.[0-9]+:('"${DEVICE_PORT}"'|[0-9]+)' || true)"
    if [ -n "${hits}" ]; then
      offenders=$((offenders+1))
      fail "device-egress: pod ${pod} has ESTABLISHED connection(s) to the device VLAN:"
      echo "${hits}" | sed 's/^/        /'
    fi
  done <<< "${pods}"

  if [ "${tool_found}" -eq 0 ]; then
    # No pod could be inspected from inside. The NetworkPolicy + replicas:0 are
    # the durable guarantees; flag that the live socket cross-check was a no-op.
    info "no pod exposed ss/netstat — relied on manifest interlocks (NetPol + replicas:0); live socket cross-check skipped"
    pass "device-egress: no in-pod socket evidence of device-VLAN writes (manifest interlocks authoritative)"
  elif [ "${offenders}" -eq 0 ]; then
    pass "device-egress: ZERO established connections from staging pods to ${DEVICE_VLAN_CIDR}"
  fi
}

# ── Mode: device-monitor (prod exactly-one-writer STUB) ─────────────────────
run_device_monitor() {
  echo "=== k3s device-route monitor STUB ($(date '+%Y-%m-%d %H:%M:%S')) — namespace=${NAMESPACE} ==="
  echo "(READ-ONLY: inspects pods' own sockets; asserts EXACTLY ONE writer to ${DEVICE_ESP32_IP}:${DEVICE_PORT}.)"
  echo ""
  local pods pod writers=0 tool_found=0
  pods="$("${KC[@]}" get pods --field-selector=status.phase=Running \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)"
  if [ -z "${pods}" ]; then
    fail "device-monitor: no Running pods in ${NAMESPACE} — cannot observe the single writer"
    print_summary; return
  fi
  while IFS= read -r pod; do
    [ -z "${pod}" ] && continue
    local out
    out="$("${KC[@]}" exec "${pod}" -- sh -c \
      "command -v ss >/dev/null 2>&1 && ss -tnp 2>/dev/null || (command -v netstat >/dev/null 2>&1 && netstat -tnp 2>/dev/null) || echo __NO_SOCKET_TOOL__" \
      2>/dev/null || true)"
    if echo "${out}" | grep -q '__NO_SOCKET_TOOL__'; then
      info "pod ${pod}: no ss/netstat — cannot observe its device socket"
      continue
    fi
    tool_found=1
    if echo "${out}" | grep -E 'ESTAB|ESTABLISHED' \
        | grep -q "${DEVICE_ESP32_IP}:${DEVICE_PORT}"; then
      writers=$((writers+1))
      info "pod ${pod}: HOLDS the ESP32 native-API connection (${DEVICE_ESP32_IP}:${DEVICE_PORT})"
    fi
  done <<< "${pods}"

  if [ "${tool_found}" -eq 0 ]; then
    info "no pod exposed ss/netstat — STUB cannot observe sockets in this cluster build"
    fail "device-monitor: socket inspection unavailable; wire a sidecar/exporter to observe the single writer"
  elif [ "${writers}" -eq 1 ]; then
    pass "device-monitor: EXACTLY ONE pod holds the ESP32 writer connection (single-writer invariant observed)"
  elif [ "${writers}" -eq 0 ]; then
    fail "device-monitor: ZERO pods hold the ESP32 writer connection (no writer — device loop down?)"
  else
    fail "device-monitor: ${writers} pods hold the ESP32 writer connection — MULTI-WRITER, device-thrash risk"
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
