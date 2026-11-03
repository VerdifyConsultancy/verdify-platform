#!/usr/bin/env bash
# dashboard-smoke.sh — the live check of dashboard.verdify.ai (#1143; architecture §10).
#
# It runs from any kubectl host with no sign-in, so a fleet verifier can rerun it. It prints only
# check names, HTTP statuses and PASS or FAIL, never a cookie, token or body, and it exits non-zero
# on any FAIL.
#
# Inside the web pod (kubectl exec, with the pod's own OPERATOR_API_TOKEN, which stays in the pod):
#   - /readyz reports status ready and the build commit: --expected-web-sha, or any full SHA without it.
#   - INT-01 mints a viewer session named dashboard-smoke (201), which proves the token the web pod
#     and the operator API share.
#   - Through the BFF on 127.0.0.1:3000, with the Host dashboard.verdify.ai and that session's cookie:
#       GET /api/v1/me answers 200, role viewer, writes_enabled false.
#       GET /api/v1/now answers 200, freshness.climate.age_s at most 300.
#       PUT /api/v1/settings/mister_engage_kpa with a foreign Origin answers 403 origin_mismatch.
#       POST /api/v1/irrigation/stop {line_id: L05} with the exact Origin answers 403 forbidden. Stop
#       is the one write the flag never gates, so it reaches the operator API's role check; L05 is
#       reserved and never open, so even a wrong answer would turn nothing off.
#   - INT-02 deletes the session (204), whatever the checks before it answered.
#
# From this host:
#   - https://api.verdify.ai/v1/me answers 404: no operator path on the public API.
#   - Anonymous GET https://dashboard.verdify.ai/ answers 302 to /auth/login?returnTo=%2F, once with the
#     LAN answer (192.168.7.10) and once through Cloudflare (the name resolved by a public resolver).
#   - /auth/login?returnTo=%2F answers 302 to Authentik's authorize endpoint with
#     client_id=verdify-dashboard and code_challenge_method=S256.
#
# What it writes: one dashboard_session row, deleted at the end. A run stopped before INT-02 leaves
# that viewer row until it expires (12 h); nobody ever saw its token. It changes no setting, line or
# device.
#
# USAGE
#   scripts/dashboard-smoke.sh [--expected-web-sha <40 hex>]
#
#   R1 ships with DASHBOARD_WRITES_ENABLED false, which this run requires; R2 (#1144), which turns the
#   flag on, changes that check.
#
# Exit code 0 = every check passed; 1 = at least one FAIL; 2 = bad usage or a missing tool.

set -uo pipefail

# Long enough for every in-pod call to use its whole 15 s deadline.
WEB_POD_DEADLINE_S=120
CURL_TIMEOUT_S=15
LAN_ANSWER="192.168.7.10"
PUBLIC_RESOLVER="https://cloudflare-dns.com/dns-query"
AUTHORIZE_URL="https://auth.vallery.net/application/o/authorize/"
EXPECTED_WEB_SHA=""

while [ $# -gt 0 ]; do
  case "$1" in
    --expected-web-sha)
      if [ $# -lt 2 ]; then echo "ERROR: --expected-web-sha needs a value." >&2; exit 2; fi
      EXPECTED_WEB_SHA="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [ -n "${EXPECTED_WEB_SHA}" ] && ! [[ "${EXPECTED_WEB_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: --expected-web-sha must be 40 hex characters." >&2
  exit 2
fi
for tool in kubectl curl; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "ERROR: ${tool} is required on PATH." >&2
    exit 2
  fi
done

PASS=0; FAIL=0
pass() { echo "  PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }

# The checks inside the web pod. stderr stays here because a crash's stack trace is not a check
# name; run_web_pod_checks passes on only the PASS and FAIL lines.
web_pod_checks() {
  exec kubectl -n verdify-dashboard exec -i deploy/verdify-dashboard-web -c web -- \
    node --input-type=module - "${EXPECTED_WEB_SHA}" 2>/dev/null <<'JS'
import { request } from "node:http";

const BFF = "http://127.0.0.1:3000";
const HOST = "dashboard.verdify.ai";
const ORIGIN = `https://${HOST}`;
const DEADLINE_MS = 15000;
const MAX_CLIMATE_AGE_S = 300;
const expectedSha = process.argv[2];
const bearer = `Bearer ${process.env.OPERATOR_API_TOKEN}`;

// A closed stdout must not stop the run before INT-02 deletes the session.
process.stdout.on("error", () => {});

// One line per check. Nothing from an answer but its status code is ever printed.
function check(name, answer, ok) {
  console.log(`${ok ? "PASS" : "FAIL"} ${name} (${answer.status ?? "no answer"})`);
}

// Resolves with the status and the parsed JSON body, or status null when nothing answered in time.
function call(url, { method = "GET", headers = {}, body } = {}) {
  const payload = body === undefined ? undefined : JSON.stringify(body);
  const sent = payload === undefined ? headers : { ...headers, "content-type": "application/json", "content-length": Buffer.byteLength(payload) };
  return new Promise((resolve) => {
    const out = request(url, { method, headers: sent, agent: false, signal: AbortSignal.timeout(DEADLINE_MS) }, (incoming) => {
      const chunks = [];
      incoming.on("data", (chunk) => chunks.push(chunk));
      incoming.on("end", () => {
        let json = null;
        try { json = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { /* the check reads null */ }
        resolve({ status: incoming.statusCode, json });
      });
      incoming.on("error", () => resolve({ status: incoming.statusCode, json: null }));
    });
    out.on("error", () => resolve({ status: null, json: null }));
    out.end(payload);
  });
}

async function checksThroughTheBff(token) {
  const bff = (path, { method = "GET", origin, body } = {}) => call(`${BFF}${path}`, {
    method,
    body,
    headers: { host: HOST, accept: "application/json", cookie: `__Host-verdify_session=${token}`, ...(origin ? { origin } : {}) },
  });

  const me = await bff("/api/v1/me");
  check("API-01 /api/v1/me reads role viewer and writes_enabled false", me, me.status === 200 && me.json?.role === "viewer" && me.json?.writes_enabled === false);

  const now = await bff("/api/v1/now");
  const age = now.json?.freshness?.climate?.age_s;
  check(`API-02 /api/v1/now reads climate at most ${MAX_CLIMATE_AGE_S} s old`, now, now.status === 200 && typeof age === "number" && age <= MAX_CLIMATE_AGE_S);

  // The code tells the Origin check from the operator API's role check, which answers 403 too.
  const foreign = await bff("/api/v1/settings/mister_engage_kpa", { method: "PUT", origin: "https://evil.example", body: {} });
  check("a cross-Origin write is refused origin_mismatch", foreign, foreign.status === 403 && foreign.json?.code === "origin_mismatch");

  const stop = await bff("/api/v1/irrigation/stop", { method: "POST", origin: ORIGIN, body: { line_id: "L05" } });
  check("the viewer's own Stop is refused forbidden", stop, stop.status === 403 && stop.json?.code === "forbidden");
}

const ready = await call(`${BFF}/readyz`);
const revision = ready.json?.source_revision;
const revisionOk = expectedSha ? revision === expectedSha : /^[0-9a-f]{40}$/.test(revision ?? "");
check(`web /readyz is ready at ${expectedSha ? "the expected commit" : "a full commit SHA"}`, ready, ready.status === 200 && ready.json?.status === "ready" && revisionOk);

const minted = await call(`${process.env.OPERATOR_API_URL}/internal/sessions`, {
  method: "POST",
  headers: { authorization: bearer, accept: "application/json" },
  body: { username: "dashboard-smoke", display_name: "dashboard smoke", role: "viewer" },
});
const token = minted.json?.token;
const mintedOk = minted.status === 201 && typeof token === "string";
check("INT-01 mints a viewer session with the web pod's token", minted, mintedOk);
if (mintedOk) {
  try {
    await checksThroughTheBff(token);
  } finally {
    const deleted = await call(`${process.env.OPERATOR_API_URL}/internal/sessions/current`, {
      method: "DELETE",
      headers: { authorization: bearer, "x-dashboard-session": token },
    });
    check("INT-02 deletes the session", deleted, deleted.status === 204);
  }
}
console.log("END");
JS
}

# Kill "$@" after $1 seconds: kubectl's request timeout does not bound a streaming exec. The in-pod
# program still ends on its own deadlines, so a killed exec does not stop its INT-02.
bounded() {
  local limit="$1" waited=0 pid
  shift
  "$@" &
  pid=$!
  while kill -0 "${pid}" 2>/dev/null; do
    if [ "${waited}" -ge "${limit}" ]; then
      kill "${pid}" 2>/dev/null
      break
    fi
    sleep 1
    waited=$((waited+1))
  done
  wait "${pid}" 2>/dev/null
}

run_web_pod_checks() {
  local line finished=0
  while IFS= read -r line; do
    case "${line}" in
      "PASS "*) pass "${line#PASS }" ;;
      "FAIL "*) fail "${line#FAIL }" ;;
      END) finished=1 ;;
    esac
  done < <(bounded "${WEB_POD_DEADLINE_S}" web_pod_checks)
  if [ "${finished}" -ne 1 ]; then
    fail "the checks in the web pod did not finish (no pod, a failed exec or a crash)"
  fi
}

# One anonymous request that follows nothing: PROBE_STATUS and PROBE_LOCATION. The headers, which can
# carry a Set-Cookie, stay in this function; "$@" holds the curl options and the URL.
probe() {
  local headers
  PROBE_STATUS=""
  PROBE_LOCATION=""
  headers="$(curl -sS -o /dev/null -D - --max-time "${CURL_TIMEOUT_S}" "$@" 2>/dev/null)" || return 0
  headers="${headers//$'\r'/}"
  PROBE_STATUS="$(awk '/^HTTP\// { code = $2 } END { print code }' <<< "${headers}")"
  PROBE_LOCATION="$(awk 'tolower($1) == "location:" { print $2; exit }' <<< "${headers}")"
}

# PASS when the last probe answered $2, and $3 (when given) equals its Location.
expect() {
  local name="$1" status="$2" location="${3:-}"
  if [ "${PROBE_STATUS}" = "${status}" ] && { [ -z "${location}" ] || [ "${PROBE_LOCATION}" = "${location}" ]; }; then
    pass "${name} (${PROBE_STATUS})"
  else
    fail "${name} (${PROBE_STATUS:-no answer})"
  fi
}

authorize_redirect_ok() {
  local query
  [[ "${PROBE_STATUS}" = "302" && "${PROBE_LOCATION}" == "${AUTHORIZE_URL}"* && "${PROBE_LOCATION}" == *\?* ]] || return 1
  query="&${PROBE_LOCATION#*\?}&"
  [[ "${query}" == *"&client_id=verdify-dashboard&"* && "${query}" == *"&code_challenge_method=S256&"* ]]
}

run_host_checks() {
  local dashboard="https://dashboard.verdify.ai"
  local lan=(--resolve "dashboard.verdify.ai:443:${LAN_ANSWER}")
  local sign_in="/auth/login?returnTo=%2F"

  probe "https://api.verdify.ai/v1/me"
  expect "https://api.verdify.ai/v1/me is not served" 404

  probe "${lan[@]}" "${dashboard}/"
  expect "anonymous / goes to sign-in on the LAN answer" 302 "${sign_in}"

  probe --doh-url "${PUBLIC_RESOLVER}" "${dashboard}/"
  expect "anonymous / goes to sign-in through Cloudflare" 302 "${sign_in}"

  probe "${lan[@]}" "${dashboard}${sign_in}"
  if authorize_redirect_ok; then
    pass "/auth/login goes to Authentik's authorize endpoint with the client and S256 (${PROBE_STATUS})"
  else
    fail "/auth/login goes to Authentik's authorize endpoint with the client and S256 (${PROBE_STATUS:-no answer})"
  fi
}

echo "=== dashboard smoke ($(date '+%Y-%m-%d %H:%M:%S')) ==="
echo "[1] inside the web pod"
run_web_pod_checks
echo ""
echo "[2] from this host"
run_host_checks
echo ""
echo "── summary ─────────────────────────────────"
echo "  passed=${PASS} failed=${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
  echo "  RESULT: NOT GREEN — review the FAIL lines above."
  exit 1
fi
echo "  RESULT: GREEN"
exit 0
