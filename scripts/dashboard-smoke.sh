#!/usr/bin/env bash
# dashboard-smoke.sh — the live checks of dashboard.verdify.ai (#1143, #1181, #1144, #1186; architecture §10).
#
# It runs from any kubectl host with no sign-in, so a fleet verifier can rerun it. It prints only
# check names, HTTP statuses, readback states and PASS or FAIL, never a cookie, token or body, and it exits non-zero
# on any FAIL. It works inside the web pod through the BFF with the pod's own OPERATOR_API_TOKEN, which stays in the
# pod, and with no flag it runs only the baseline. Every leg below runs only when it is named.
#
# USAGE
#   scripts/dashboard-smoke.sh [--expected-web-sha <40 hex>]
#   scripts/dashboard-smoke.sh --sweep [--expected-web-sha <40 hex>]
#   scripts/dashboard-smoke.sh --agent-write <name> [--param <setting>] [--expected-web-sha <40 hex>]
#   scripts/dashboard-smoke.sh --role operator [--param <setting>] [--expected-web-sha <40 hex>]
#   scripts/dashboard-smoke.sh --role operator --revert [--param <setting>] [--expected-web-sha <40 hex>]
#   scripts/dashboard-smoke.sh --role operator --lights [--expected-web-sha <40 hex>]
#
# Every run in the web pod starts with /readyz: status ready at --expected-web-sha, or at any full SHA without it;
# --agent-write reads it after the Job, for API-17. <setting> defaults to mister_engage_kpa.
#
# BASELINE (no flag). Inside the web pod:
#   - INT-01 mints a viewer session named dashboard-smoke (201), which proves the token the web pod
#     and the operator API share.
#   - Through the BFF on 127.0.0.1:3000, with the Host dashboard.verdify.ai and that session's cookie:
#       GET /api/v1/me answers 200, role viewer, writes_enabled as the pod's DASHBOARD_WRITES_ENABLED says (the line
#       prints it, which is R2's record of the first true and of a flag rollback).
#       GET /api/v1/now answers 200, freshness.climate.age_s at most 300.
#       PUT /api/v1/settings/mister_engage_kpa with a foreign Origin answers 403 origin_mismatch.
#       POST /api/v1/irrigation/stop {line_id: L05} with the exact Origin answers 403 forbidden. Stop
#       is the one write the flag never gates, so it reaches the operator API's role check; L05 is
#       reserved and never open, so even a wrong answer would turn nothing off.
#   - INT-02 deletes the session (204), whatever the checks before it answered.
# From this host:
#   - https://api.verdify.ai/v1/me answers 404: no operator path on the public API.
#   - Anonymous GET https://dashboard.verdify.ai/ answers 302 to /auth/login?returnTo=%2F, once with the
#     LAN answer (192.168.7.10) and once through Cloudflare (the name resolved by a public resolver).
#   - /auth/login?returnTo=%2F answers 302 to Authentik's authorize endpoint with
#     client_id=verdify-dashboard and code_challenge_method=S256.
# What it writes: one dashboard_session row, deleted at the end. A run stopped before INT-02 leaves
# that viewer row until it expires (12 h); nobody ever saw its token. It changes no setting, line or device.
#
# --sweep (#1181 step 4, #1186 step 1): reads only, as the viewer, so it runs at any time.
#   - GET each served read through the BFF and expect 200: API-01 to API-07 (API-06 also with zone=south), API-09,
#     API-12, API-13 (with its three reasons present), API-14, API-17, API-19 (zone_vpd_target_center_sm at the
#     midpoint of its API-09 min and max), API-21, API-IRR-01, API-IRR-02, API-IRR-10 and API-IRR-11. A failure prints
#     the id and the status, so a read the dashboard login was never granted fails here as a 500, not on a screen.
#   - API-IRR-10 reads 2025-08-05 to 2025-09-03, the first 30 days of wet-relay rows (irrigation §6.2), and each of
#     those days must carry minutes, not no_data. The minutes begin there, so an earlier window reads no_data for good.
#   - Parity: inside a verdify-mcp pod, with the Iris token already in its environment, MCP irrigation_status; at the
#     same second, /api/v1/irrigation through the BFF. Each pod prints a hash per field and this host compares them,
#     so no value leaves either pod. lines and program are compared field by field, ignoring server_time, observed_at
#     and age fields, and the line says equal or names the differing line ids and fields. A difference is read up to
#     three times, because a run in progress can move between two reads.
#
# --agent-write <name> (#1144 step 3): live from the sync, because the flag gates only the BFF.
#   - Needs the agent's key in Secret verdify-prod/verdify-mcp-agents (read by key name only). Without it the leg is a
#     FAIL that says so, and no Job runs.
#   - A one-shot Job in verdify-prod, label verdify.ai/mcp-agent "true", the live verdify-mcp digest, no ServiceAccount
#     token, restricted, backoffLimit 0, activeDeadlineSeconds 120, with that key as an env var. It retries its first
#     connect for up to 30 s, reads the setting through MCP settings, refuses unless the newest desired row is absent or
#     released, then calls set_setting with the device value plus one step (minus at the max) and set_setting(release=
#     true), and prints only the returned state and codes (held under an armed experiment, which is not a failure here:
#     this leg waits for no readback). A SIGTERM still runs the release. A Job that ended Complete with no write in its
#     log is a FAIL, because nothing then shows what it did.
#   - This host deletes the Job, and a viewer session reads API-17 ?source=agent:<name>&target=<setting>: both writes
#     with actor source agent:<name> and name <name>, which Activity shows as "<name> · agent".
#
# --role operator (#1144 step 6), --role operator --revert (#1186 step 1): need writes_enabled true, so they refuse to
# start with the flag off. An operator session named dashboard-smoke, the exact Origin and a new Idempotency-Key on
# each write. They refuse to start unless the setting's API-09 row is a writable numeric one with a device value and
# its newest desired row is absent or released; a crop-band or irrigation-site setting is refused too, because a
# release does not undo the first and the second cannot confirm before the engine firmware.
#   - --role operator: a viewer's same PUT answers 403 forbidden and adds no row. API-10 writes the device value plus
#     one step (minus at the max), manual, and answers requested, or held under an armed experiment; API-09, polled
#     every 5 s, reads confirmed (FAIL after 5 minutes; held by an armed experiment prints HELD with its detail, so
#     repeat it after disarm or with another --param). API-11 releases it, the row reads released and the device leaves
#     the check's value within 5 minutes, for its plan, band or default value. API-17 lists setting.write, then
#     setting.release, source dashboard, actor dashboard-smoke.
#   - --revert: API-10 writes one step, then the device value, each manual and each read to confirmed; then, as the
#     drawer does, the desired row before the newest in API-12, back through API-10: requested, then confirmed. API-11
#     releases it.
#
# --role operator --lights (#1186 step 1): SWITCHES A REAL LIGHT, once each way. It needs writes_enabled true and
# refuses to start unless the main light's API-13 intent and Lutron state are known and equal and
# sw_gl_main_auto_mode reads 1 with its newest desired row absent or released.
#   - API-20 to the opposite of the intent: 200, or a 503 whose result it prints (Lutron may still switch late). API-13,
#     polled every 5 s, must read that state within 30 s.
#   - Back to automatic as /equipment does it: API-10 sw_gl_main_auto_mode 1, manual, requested, then API-09 confirmed
#     within 5 minutes. With the intent and Lutron known and different, one API-20 to the intent, read within 30 s. That
#     runs even after a failure above, so the leg ends with the light at the intent, unless a person wrote
#     sw_gl_main_auto_mode meanwhile, or API-09 cannot show that nobody did: then the leg touches neither it nor the light
#     again, and a FAIL says so.
#   - API-11 releases sw_gl_main_auto_mode. API-17 lists the light.on and light.off rows it sent (target
#     grow_light_main) and the write and release (target sw_gl_main_auto_mode), all by dashboard-smoke.
#
# Every write leg writes one grid step at most and undoes it, and it never overwrites a value it did not write: each
# write leg re-reads the newest desired row before releasing and leaves another writer's row alone (a FAIL says so).
# Its release runs on every path; the in-pod program runs on to its own deadlines if this host's exec dies. Only a pod
# that dies mid-leg leaves a row to release by hand: docs/runbooks/dashboard.md, "Live checks".
#
# Exit code 0 = every check passed; 1 = at least one FAIL or HELD; 2 = bad usage or a missing tool.

set -uo pipefail

CURL_TIMEOUT_S=15
LAN_ANSWER="192.168.7.10"
PUBLIC_RESOLVER="https://cloudflare-dns.com/dns-query"
AUTHORIZE_URL="https://auth.vallery.net/application/o/authorize/"
DEFAULT_PARAM="mister_engage_kpa"
# How long the MCP Job may take: its own activeDeadlineSeconds and a margin for the Job to read Failed.
AGENT_JOB_WAIT_S=150
# Parity: the seconds both reads wait so that they start together, and how often a difference is read.
PARITY_LEAD_S=4
PARITY_DEADLINE_S=60
PARITY_ATTEMPTS=3

EXPECTED_WEB_SHA=""
ROLE=""
PARAM=""
AGENT=""
AGENT_GIVEN=0
SWEEP=0
LIGHTS=0
REVERT=0

usage_error() {
  echo "ERROR: $1" >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --expected-web-sha|--role|--agent-write|--param)
      if [ $# -lt 2 ]; then usage_error "$1 needs a value."; fi
      case "$1" in
        --expected-web-sha) EXPECTED_WEB_SHA="$2" ;;
        --role) ROLE="$2" ;;
        --agent-write) AGENT="$2"; AGENT_GIVEN=1 ;;
        --param) PARAM="$2" ;;
      esac
      shift 2 ;;
    --sweep) SWEEP=1; shift ;;
    --lights) LIGHTS=1; shift ;;
    --revert) REVERT=1; shift ;;
    -h|--help)
      awk 'NR > 1 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "$0"
      exit 0 ;;
    *) usage_error "unknown argument: $1" ;;
  esac
done
if [ -n "${EXPECTED_WEB_SHA}" ] && ! [[ "${EXPECTED_WEB_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  usage_error "--expected-web-sha must be 40 hex characters."
fi
if [ -n "${ROLE}" ] && [ "${ROLE}" != "operator" ]; then
  usage_error "--role takes only operator; every other leg is the viewer or the agent already."
fi

# The one leg this run names. WEB_POD_DEADLINE_S is long enough for that leg's every in-pod call to use its whole 15 s
# deadline and every wait its whole limit, so the host only gives up on a pod that stopped answering.
LEG="baseline"
WEB_POD_DEADLINE_S=120
if [ "${SWEEP}" -eq 1 ]; then
  LEG="sweep"
  WEB_POD_DEADLINE_S=420
  if [ "${AGENT_GIVEN}${LIGHTS}${REVERT}" != "000" ] || [ -n "${ROLE}${PARAM}" ]; then
    usage_error "--sweep runs alone, as the viewer."
  fi
elif [ "${AGENT_GIVEN}" -eq 1 ]; then
  LEG="agent"
  if [ "${LIGHTS}${REVERT}" != "00" ] || [ -n "${ROLE}" ]; then
    usage_error "--agent-write runs alone, as the agent."
  fi
  if ! [[ "${AGENT}" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] || [ "${AGENT}" = "admin" ]; then
    usage_error "--agent-write takes an agent's name as Secret verdify-mcp-agents keys it: ^[a-z0-9][a-z0-9_-]{0,62}$, never admin."
  fi
elif [ -n "${ROLE}" ]; then
  if [ "${LIGHTS}${REVERT}" = "11" ]; then usage_error "--lights and --revert are two runs."; fi
  if [ "${LIGHTS}" -eq 1 ] && [ -n "${PARAM}" ]; then usage_error "--lights writes sw_gl_main_auto_mode; it takes no --param."; fi
  LEG="operator"
  WEB_POD_DEADLINE_S=1000
  if [ "${LIGHTS}" -eq 1 ]; then LEG="lights"; fi
  if [ "${REVERT}" -eq 1 ]; then LEG="revert"; WEB_POD_DEADLINE_S=1500; fi
elif [ "${LIGHTS}${REVERT}" != "00" ]; then
  usage_error "--lights and --revert write as the operator: add --role operator."
elif [ -n "${PARAM}" ]; then
  usage_error "--param goes with --agent-write, --role operator or --revert."
fi
if [ -n "${PARAM}" ] && ! [[ "${PARAM}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  usage_error "--param must match ^[a-z][a-z0-9_]*$."
fi
PARAM="${PARAM:-${DEFAULT_PARAM}}"

TOOLS="kubectl"
if [ "${LEG}" = "baseline" ]; then TOOLS="kubectl curl"; fi
for tool in ${TOOLS}; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    echo "ERROR: ${tool} is required on PATH." >&2
    exit 2
  fi
done

PASS=0; FAIL=0
pass() { echo "  PASS  $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
# Not a pass: the check is repeated after disarm or with another --param.
held() { echo "  HELD  $1"; FAIL=$((FAIL+1)); }

# What a stopped run must not leave behind. The Job's own ttlSecondsAfterFinished covers a kill -9 here.
AGENT_JOB=""
WORK_DIR=""
cleanup() {
  if [ -n "${AGENT_JOB}" ]; then
    kubectl -n verdify-prod delete job "${AGENT_JOB}" --wait=false >/dev/null 2>&1
  fi
  if [ -n "${WORK_DIR}" ]; then rm -rf "${WORK_DIR}"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# The checks inside the web pod. "$@" is the leg and its arguments, nothing for the baseline. stderr stays here
# because a crash's stack trace is not a check name; run_web_pod_checks passes on only the PASS, FAIL and HELD lines.
web_pod_checks() {
  exec kubectl -n verdify-dashboard exec -i deploy/verdify-dashboard-web -c web -- \
    node --input-type=module - "${EXPECTED_WEB_SHA}" "$@" 2>/dev/null <<'JS'
import { createHash, randomUUID } from "node:crypto";
import { request } from "node:http";
import { setTimeout as sleep } from "node:timers/promises";

const BFF = "http://127.0.0.1:3000";
const HOST = "dashboard.verdify.ai";
const ORIGIN = `https://${HOST}`;
const DEADLINE_MS = 15000;
const MAX_CLIMATE_AGE_S = 300;
const DAY_MS = 86400000;
const POLL_MS = 5000;
const CONFIRM_MS = 300000;
const LUTRON_MS = 30000;
const RELEASE_TRIES = 3;
const SMOKE_USER = "dashboard-smoke";
const R2_REASON = "R2 live check";
const R4_REASON = "R4 live check";
const MAIN_LIGHT = "grow_light_main";
const MAIN_AUTO = "sw_gl_main_auto_mode";
const BAND_PARAM = "zone_vpd_target_center_sm";
const REASONS = ["mode_reason", "fog_block_reason", "moisture_block_reason"];
// The first 30 days of wet-relay rows (irrigation §6.2): the minutes begin here.
const MINUTES_DAYS = 30;
const MINUTES_PATH = "/api/v1/irrigation/minutes?from=2025-08-05&to=2025-09-03&group_by=zone";
// A release does not undo a crop-band write, and an irrigation-site write cannot confirm before the engine firmware.
const NOT_CHECKABLE = new Set(["crop_band", "irrigation_site"]);
const [expectedSha, leg = "baseline", ...legArgs] = process.argv.slice(2);
const bearer = `Bearer ${process.env.OPERATOR_API_TOKEN}`;
// Where this run's API-12 and API-17 reads start; a minute early, because the pod's clock is not the database's.
const windowStart = new Date(Date.now() - 60000).toISOString();

// A closed stdout must not stop the run before a release or INT-02.
process.stdout.on("error", () => {});

// One line per check. Nothing from an answer but its status code and readback state is ever printed.
function check(name, answer, ok) {
  console.log(`${ok ? "PASS" : "FAIL"} ${name} (${answer.status ?? "no answer"})`);
}
function said(ok, name, detail) {
  console.log(`${ok ? "PASS" : "FAIL"} ${name} (${detail})`);
}
const secondsSince = (since) => Math.round((Date.now() - since) / 1000);

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

// The session's calls through the BFF. A write passes origin and its Idempotency-Key as key.
const throughTheBff = (token) => (path, { method = "GET", origin, body, key } = {}) => call(`${BFF}${path}`, {
  method,
  body,
  headers: {
    host: HOST,
    accept: "application/json",
    cookie: `__Host-verdify_session=${token}`,
    ...(origin ? { origin } : {}),
    ...(key ? { "idempotency-key": key } : {}),
  },
});

// INT-01, the work, then INT-02 whatever the work answered.
async function withSession(role, work) {
  const minted = await call(`${process.env.OPERATOR_API_URL}/internal/sessions`, {
    method: "POST",
    headers: { authorization: bearer, accept: "application/json" },
    body: { username: SMOKE_USER, display_name: "dashboard smoke", role },
  });
  const token = minted.json?.token;
  const mintedOk = minted.status === 201 && typeof token === "string";
  check(`INT-01 mints the ${role} session with the web pod's token`, minted, mintedOk);
  if (!mintedOk) return;
  try {
    await work(throughTheBff(token));
  } finally {
    const deleted = await call(`${process.env.OPERATOR_API_URL}/internal/sessions/current`, {
      method: "DELETE",
      headers: { authorization: bearer, "x-dashboard-session": token },
    });
    check(`INT-02 deletes the ${role} session`, deleted, deleted.status === 204);
  }
}

async function baseline(bff) {
  const flag = process.env.DASHBOARD_WRITES_ENABLED === "true";
  const me = await bff("/api/v1/me");
  check(`API-01 /api/v1/me reads role viewer and writes_enabled ${flag}, as the pod's flag says`, me, me.status === 200 && me.json?.role === "viewer" && me.json?.writes_enabled === flag);

  const now = await bff("/api/v1/now");
  const age = now.json?.freshness?.climate?.age_s;
  check(`API-02 /api/v1/now reads climate at most ${MAX_CLIMATE_AGE_S} s old`, now, now.status === 200 && typeof age === "number" && age <= MAX_CLIMATE_AGE_S);

  // The code tells the Origin check from the operator API's role check, which answers 403 too.
  const foreign = await bff("/api/v1/settings/mister_engage_kpa", { method: "PUT", origin: "https://evil.example", body: {} });
  check("a cross-Origin write is refused origin_mismatch", foreign, foreign.status === 403 && foreign.json?.code === "origin_mismatch");

  const stop = await bff("/api/v1/irrigation/stop", { method: "POST", origin: ORIGIN, body: { line_id: "L05" } });
  check("the viewer's own Stop is refused forbidden", stop, stop.status === 403 && stop.json?.code === "forbidden");
}

// ── The read sweep (#1181 step 4, #1186 step 1) ────────────────────────────

function carriesMinutes(json) {
  const days = json?.periods;
  if (json?.group_by !== "zone" || !Array.isArray(days) || days.length !== MINUTES_DAYS || !Array.isArray(json.rows)) return false;
  return days.every((day) => day.coverage !== "no_data" && json.rows.some((row) => row.period === day.start && row.minutes > 0));
}

async function sweep(bff) {
  const now = Date.now();
  const ago = (ms) => new Date(now - ms).toISOString();
  const end = new Date(now).toISOString();
  const lastDay = `from=${ago(DAY_MS)}&to=${end}`;
  const rows = [
    ["API-01", "/api/v1/me", (json) => json?.role === "viewer"],
    ["API-02", "/api/v1/now"],
    ["API-03", "/api/v1/metrics"],
    ["API-04", `/api/v1/series?metric=climate.temp_avg&${lastDay}`],
    ["API-05", `/api/v1/states?${lastDay}`],
    ["API-06", `/api/v1/band?${lastDay}`],
    ["API-06 zone=south", `/api/v1/band?zone=south&${lastDay}`],
    ["API-07", "/api/v1/plan"],
    ["API-09", "/api/v1/settings"],
    ["API-12", `/api/v1/settings/mister_engage_kpa/history?${lastDay}`],
    ["API-13 with its three reasons", "/api/v1/equipment", (json) => REASONS.every((reason) => json?.[reason] !== undefined && json[reason] !== null)],
    ["API-14", "/api/v1/alerts"],
    ["API-17", "/api/v1/activity?limit=50"],
    ["API-21", "/api/v1/water/daily?days=90"],
    ["API-IRR-01", "/api/v1/irrigation"],
    ["API-IRR-02", "/api/v1/irrigation/schedules?days=7&past_days=7"],
    [`API-IRR-10 with minutes on all ${MINUTES_DAYS} days, not no_data`, MINUTES_PATH, carriesMinutes],
    ["API-IRR-11", `/api/v1/irrigation/runs?from=${ago(7 * DAY_MS)}&to=${end}`],
  ];
  let settings = null;
  for (const [id, path, more] of rows) {
    const answer = await bff(path);
    check(id, answer, answer.status === 200 && (!more || more(answer.json)));
    if (id === "API-09") settings = answer.json;
  }
  const band = settings?.items?.find((item) => item.param === BAND_PARAM);
  if (!Number.isFinite(band?.min) || !Number.isFinite(band?.max)) {
    said(false, "API-19", `no min and max for ${BAND_PARAM} in API-09`);
    return;
  }
  const preview = await bff(`/api/v1/band/preview?param=${BAND_PARAM}&value=${(band.min + band.max) / 2}`);
  check("API-19", preview, preview.status === 200);
}

// ── MCP parity: one hash per field, so no value leaves the pod ─────────────

const IGNORED_FIELD = /^(server_time|observed_at|age_s)$|_age_s$/;

function scalarText(value) {
  if (typeof value === "number") return `num:${Number.isInteger(value) && Math.abs(value) < 1e15 ? value : value.toFixed(9)}`;
  return typeof value === "string" ? `str:${value}` : String(value);
}
function printFields(value, path) {
  if (Array.isArray(value) && value.length > 0) value.forEach((item, index) => printFields(item, `${path}[${index}]`));
  else if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0) {
    for (const key of Object.keys(value)) if (!IGNORED_FIELD.test(key)) printFields(value[key], `${path}.${key}`);
  } else {
    const text = Array.isArray(value) ? "[]" : value && typeof value === "object" ? "{}" : scalarText(value);
    console.log(`FIELD ${path} ${createHash("sha256").update(text).digest("hex").slice(0, 16)}`);
  }
}
async function parity(bff, at) {
  await sleep(Math.max(0, at * 1000 - Date.now()));
  const readAt = Date.now();
  const answer = await bff("/api/v1/irrigation");
  const ok = answer.status === 200 && Array.isArray(answer.json?.lines) && answer.json?.program;
  check("API-IRR-01 for the parity read", answer, ok);
  if (!ok) return;
  console.log(`AT ${readAt}`);
  for (const line of answer.json.lines) printFields(line, line.line_id);
  printFields(answer.json.program, "program");
}

// ── Settings: the guard, one write, its confirmation and its release ───────

async function settingRow(bff, param) {
  const answer = await bff("/api/v1/settings");
  const row = answer.status === 200 ? answer.json?.items?.find((item) => item.param === param) ?? null : null;
  return { answer, row };
}

const isSmoke = (actor) => actor?.source === "dashboard" && actor?.name === SMOKE_USER;

async function writesAreOn(bff) {
  const me = await bff("/api/v1/me");
  const ok = me.status === 200 && me.json?.role === "operator" && me.json?.writes_enabled === true;
  const words = me.json?.writes_enabled === false
    ? "writes are off: this leg waits for DASHBOARD_WRITES_ENABLED and wrote nothing"
    : "API-01 /api/v1/me reads role operator and writes_enabled true";
  check(words, me, ok);
  return ok;
}

function whyNotCheckable(row) {
  if (!row) return "not in API-09";
  if (!row.writable) return "not writable";
  if (row.kind !== "numeric" || NOT_CHECKABLE.has(row.control_class)) return `one step of a ${row.kind} ${row.control_class} setting cannot be undone or confirmed`;
  if (row.desired && row.desired.value !== null) return "its newest desired row is neither absent nor released, so it stays";
  if (!Number.isFinite(row.readback?.value)) return "no device value";
  if (!(row.step > 0)) return "no step";
  return null;
}

// The guard: the row, or null after a FAIL line.
async function checkableRow(bff, param) {
  const { answer, row } = await settingRow(bff, param);
  const why = answer.status === 200 ? whyNotCheckable(row) : "API-09 did not answer 200";
  check(why ? `${param} refused: ${why}` : `${param} can take the check: its newest desired row is absent or released`, answer, why === null);
  return why === null ? row : null;
}

// The device value plus one grid step, minus at the max. The server snaps it again, so its answer is what counts.
function oneStep(row) {
  const onGrid = (value) => Number((Math.round(value / row.step) * row.step).toFixed(9));
  const up = onGrid(row.readback.value + row.step);
  return row.max == null || up <= row.max ? up : onGrid(row.readback.value - row.step);
}

// API-10, with the same key if an answer is lost: the row, or null after a FAIL line. An armed experiment serves the
// policy fields, so the write's own answer already reads held, and untilConfirmed then prints HELD.
async function writeSetting(bff, param, value, reason, what) {
  const key = randomUUID();
  let answer = { status: null };
  for (let attempt = 0; attempt < RELEASE_TRIES && answer.status === null; attempt++) {
    answer = await bff(`/api/v1/settings/${param}`, { method: "PUT", origin: ORIGIN, key, body: { value, manual: true, reason } });
  }
  const held = answer.json?.state === "held" && answer.json?.state_reason?.code === "experiment";
  const ok = answer.status === 200 && (answer.json?.state === "requested" || held) && Number.isInteger(answer.json?.id);
  check(`API-10 writes ${param} ${what} and answers ${held ? "held" : "requested"}`, answer, ok);
  return ok ? { id: answer.json.id, value: answer.json.value, key } : null;
}

// API-09 every 5 s until this write reads confirmed: true, or false after its FAIL or HELD line.
async function untilConfirmed(bff, param, written) {
  const begun = Date.now();
  let last = "no answer";
  for (;;) {
    const { row } = await settingRow(bff, param);
    if (row) {
      if (row.desired?.id !== written.id) {
        said(false, `API-09 reads ${param} confirmed`, "another desired row took its place");
        return false;
      }
      const reason = row.desired.state_reason ?? null;
      if (row.state === "confirmed") {
        said(true, `API-09 reads ${param} confirmed`, `after ${secondsSince(begun)} s`);
        return true;
      }
      if (row.state === "held" && reason?.code === "experiment") {
        const detail = reason.detail ? String(reason.detail).replace(/[^\x20-\x7e]/g, " ").slice(0, 200) : "no detail";
        console.log(`HELD API-09 holds ${param} for an armed experiment (${detail}): repeat after disarm or with another --param`);
        return false;
      }
      last = reason?.code ? `${row.state}, ${reason.code}` : row.state;
    }
    if (Date.now() - begun >= CONFIRM_MS) {
      said(false, `API-09 reads ${param} confirmed within ${CONFIRM_MS / 1000} s`, `last read ${last}`);
      return false;
    }
    await sleep(POLL_MS);
  }
}

// API-11 for this check's write, never another writer's: { id, key } once released, else null after its line. It keeps
// one key across tries, so an answer lost in transit replays the same release.
async function release(bff, param, reason) {
  const key = randomUUID();
  let sent = false;
  let last = "no answer";
  for (let attempt = 0; attempt < RELEASE_TRIES; attempt++) {
    if (attempt > 0) await sleep(POLL_MS);
    const { answer: read, row } = await settingRow(bff, param);
    if (!row) {
      last = read.status ?? "no answer";
      continue;
    }
    const newest = row.desired;
    if (!newest || newest.value === null) {
      if (sent) {
        said(true, `API-11 releases ${param}`, "its answer was lost; the row reads released");
        return { id: newest?.id, key };
      }
      said(true, `${param} holds no write of this check's, so nothing is released`, read.status);
      return null;
    }
    if (!isSmoke(newest.actor)) {
      said(false, `${param}'s newest desired row is another writer's, so it stays and this check releases nothing`, read.status);
      return null;
    }
    sent = true;
    const answer = await bff(`/api/v1/settings/${param}?reason=${encodeURIComponent(reason)}`, { method: "DELETE", origin: ORIGIN, key });
    if (answer.status === 200 && answer.json?.state === "released") {
      check(`API-11 releases ${param}`, answer, true);
      return { id: answer.json.id, key };
    }
    last = answer.status ?? "no answer";
    if (answer.status !== null && answer.status < 500) break;
  }
  said(false, `${param} may still hold this check's value: release it on Settings`, last);
  return null;
}

// After a release: the row reads released, or a FAIL says what it reads.
async function readsReleased(bff, param, released) {
  const { answer, row } = await settingRow(bff, param);
  check(`API-09 reads ${param} released`, answer, row?.state === "released" && row.desired?.id === released.id && row.desired.value === null);
}

// The next pass returns the setting to its plan, band or default value, so the device leaves the check's value; the one
// case that stays is a plan now in force at that value.
async function untilBack(bff, param, written, step) {
  const near = (a, b) => Number.isFinite(a) && Math.abs(a - b) < step / 2;
  const begun = Date.now();
  let last = "no answer";
  for (;;) {
    const { row } = await settingRow(bff, param);
    if (row) {
      const device = row.readback?.value;
      const planHolds = row.applied_from === "plan" && near(row.plan?.value, written);
      if ((Number.isFinite(device) && !near(device, written)) || planHolds) {
        said(true, `the device leaves the check's ${param} once it is released`, `applied from ${row.applied_from}, after ${secondsSince(begun)} s`);
        return;
      }
      last = `applied from ${row.applied_from}`;
    }
    if (Date.now() - begun >= CONFIRM_MS) {
      said(false, `the device leaves the check's ${param} within ${CONFIRM_MS / 1000} s of the release`, `last read ${last}`);
      return;
    }
    await sleep(POLL_MS);
  }
}

// API-17 since this run began, newest first.
async function activitySinceStart(bff, query) {
  const answer = await bff(`/api/v1/activity?${query}&from=${windowStart}&limit=200`);
  return { answer, rows: answer.status === 200 && Array.isArray(answer.json?.items) ? answer.json.items : [] };
}

// Both writes, the write first, source dashboard and actor dashboard-smoke.
function bothWritesListed(rows, written, released) {
  const write = rows.findIndex((row) => row.request_id === written.key);
  const freed = rows.findIndex((row) => row.request_id === released.key);
  return write >= 0 && freed >= 0 && freed < write
    && rows[write].action === "setting.write" && isSmoke(rows[write].actor)
    && rows[freed].action === "setting.release" && isSmoke(rows[freed].actor);
}

// ── --role operator (#1144 step 6) ─────────────────────────────────────────

async function operatorWrite(bff, viewer, param) {
  if (!(await writesAreOn(bff))) return;
  const row = await checkableRow(bff, param);
  if (!row) return;
  const value = oneStep(row);
  let written = null;
  let confirmed = false;
  let released = null;
  try {
    const refused = await viewer(`/api/v1/settings/${param}`, { method: "PUT", origin: ORIGIN, key: randomUUID(), body: { value, manual: true, reason: R2_REASON } });
    check("a viewer's same API-10 is refused forbidden", refused, refused.status === 403 && refused.json?.code === "forbidden");
    const after = await settingRow(bff, param);
    check("the viewer's API-10 added no desired row", after.answer, after.answer.status === 200 && (after.row?.desired?.id ?? null) === (row.desired?.id ?? null));
    written = await writeSetting(bff, param, value, R2_REASON, "one grid step, manual,");
    if (written) confirmed = await untilConfirmed(bff, param, written);
  } finally {
    released = await release(bff, param, R2_REASON);
  }
  if (!written) return;
  if (!released) {
    said(false, "API-17 lists setting.write, then setting.release", "this check released nothing");
    return;
  }
  await readsReleased(bff, param, released);
  if (confirmed) await untilBack(bff, param, written.value, row.step);
  const { answer, rows } = await activitySinceStart(bff, `source=dashboard&target=${param}`);
  check(`API-17 lists setting.write, then setting.release, source dashboard, actor ${SMOKE_USER}`, answer, bothWritesListed(rows, written, released));
}

// ── --role operator --revert (#1186 step 1) ────────────────────────────────

async function revertLeg(bff, param) {
  if (!(await writesAreOn(bff))) return;
  const row = await checkableRow(bff, param);
  if (!row) return;
  let released = null;
  try {
    const stepped = await writeSetting(bff, param, oneStep(row), R4_REASON, "one grid step, manual,");
    if (!stepped || !(await untilConfirmed(bff, param, stepped))) return;
    const back = await writeSetting(bff, param, row.readback.value, R4_REASON, "the device value, manual,");
    if (!back || !(await untilConfirmed(bff, param, back))) return;
    // As the drawer does (dashboard/app/lib/revert.ts): the desired row before the newest, back through API-10.
    const to = new Date(Date.now() + 60000).toISOString();
    const history = await bff(`/api/v1/settings/${param}/history?from=${windowStart}&to=${to}`);
    const desired = Array.isArray(history.json?.desired) ? history.json.desired : [];
    const previous = desired.at(-2);
    const found = history.status === 200 && desired.at(-1)?.id === back.id && previous?.id === stepped.id;
    check("API-12 names the desired row before the newest", history, found);
    if (!found) return;
    const reverted = await writeSetting(bff, param, previous.value, R4_REASON, "back to that row (Revert), manual,");
    if (reverted) await untilConfirmed(bff, param, reverted);
  } finally {
    released = await release(bff, param, R4_REASON);
  }
  if (released) await readsReleased(bff, param, released);
}

// ── --role operator --lights (#1186 step 1) ────────────────────────────────

const known = (state) => state === "on" || state === "off";
const mainLight = (board) => (Array.isArray(board.json?.items) ? board.json.items.find((item) => item.equipment === MAIN_LIGHT) ?? null : null);

// API-20: true when it may have switched, so the readback decides; a light never passes on this answer. A 4xx switched
// nothing, and anything else may have: a 5xx, no answer or a 200 it cannot read.
async function switchMain(bff, state, sent) {
  const key = randomUUID();
  const answer = await bff(`/api/v1/equipment/lights/main/${state}`, { method: "POST", origin: ORIGIN, key, body: { reason: R4_REASON } });
  const confirmed = answer.status === 200 && answer.json?.result === "confirmed";
  const refused = answer.status !== null && answer.status >= 400 && answer.status < 500;
  if (confirmed) check(`API-20 main ${state} answers confirmed`, answer, true);
  else if (refused) check(`API-20 main ${state}`, answer, false);
  else said(true, `API-20 main ${state} has no confirmation yet, and Lutron may still switch late`, `${answer.status ?? "no answer"}${answer.json?.result ? ` ${String(answer.json.result).replace(/[^a-z_]/g, "")}` : ""}`);
  if (!refused) sent.push({ state, key });
  return !refused;
}

async function untilLutron(bff, state) {
  const begun = Date.now();
  let last = "no answer";
  for (;;) {
    const item = mainLight(await bff("/api/v1/equipment"));
    if (item) {
      last = item.state;
      if (item.state === state) {
        said(true, `API-13 reads Lutron ${state} on the main light`, `after ${secondsSince(begun)} s`);
        return;
      }
    }
    if (Date.now() - begun >= LUTRON_MS) {
      said(false, `API-13 reads Lutron ${state} on the main light within ${LUTRON_MS / 1000} s`, `last read ${last}`);
      return;
    }
    await sleep(POLL_MS);
  }
}

// Whether this leg may still touch the automatic switch and the light, or a FAIL line. A person's row has the last word,
// and so does a read that failed, because it cannot show there is none. afterItsWrite lets this leg's own row through.
async function switchStillFree(bff, afterItsWrite) {
  let read = await settingRow(bff, MAIN_AUTO);
  for (let attempt = 1; attempt < RELEASE_TRIES && !read.row; attempt++) {
    await sleep(POLL_MS);
    read = await settingRow(bff, MAIN_AUTO);
  }
  const newest = read.row?.desired;
  if (read.row && (!newest || newest.value === null || (afterItsWrite && isSmoke(newest.actor)))) return true;
  const why = read.row ? "has another writer's row now" : "could not be read";
  said(false, `${MAIN_AUTO} ${why}, so this leg leaves it and the light as they are`, read.answer.status ?? "no answer");
  return false;
}

async function lights(bff) {
  if (!(await writesAreOn(bff))) return;
  const board = await bff("/api/v1/equipment");
  const item = mainLight(board);
  const steady = board.status === 200 && known(item?.intent) && item.intent === item.state;
  check("API-13 reads the main light's intent and Lutron state known and equal", board, steady);
  const auto = await settingRow(bff, MAIN_AUTO);
  const automatic = auto.answer.status === 200 && auto.row?.readback?.value === 1 && (!auto.row.desired || auto.row.desired.value === null);
  check(`${MAIN_AUTO} reads 1 with its newest desired row absent or released`, auto.answer, automatic);
  if (!steady || !automatic) return;
  const sent = [];
  if (!(await switchMain(bff, item.intent === "on" ? "off" : "on", sent))) return;
  let written = null;
  let released = null;
  try {
    await untilLutron(bff, sent[0].state);
  } finally {
    // Back to automatic, as /equipment does it. It runs whatever the readback above said, because a light that
    // answered late may have switched, and the leg must end with the light at the controller's intent. A person who
    // takes the automatic switch at any point in this has the last word: this leg touches neither it nor the light again.
    if (await switchStillFree(bff, false)) {
      try {
        written = await writeSetting(bff, MAIN_AUTO, 1, R4_REASON, "1, manual,");
        if (written) await untilConfirmed(bff, MAIN_AUTO, written);
        if (await switchStillFree(bff, true)) {
          const now = mainLight(await bff("/api/v1/equipment"));
          if (known(now?.intent) && known(now?.state) && now.intent !== now.state && (await switchMain(bff, now.intent, sent))) {
            await untilLutron(bff, now.intent);
          }
        }
      } finally {
        released = await release(bff, MAIN_AUTO, R4_REASON);
      }
    }
  }
  const finalBoard = await bff("/api/v1/equipment");
  const finalItem = mainLight(finalBoard);
  check("API-13 reads the main light at the controller's intent", finalBoard, known(finalItem?.intent) && finalItem.intent === finalItem.state);
  const lightRows = await activitySinceStart(bff, `source=dashboard&target=${MAIN_LIGHT}`);
  for (const { state, key } of sent) {
    const row = lightRows.rows.find((entry) => entry.request_id === key);
    check(`API-17 lists light.${state} by ${SMOKE_USER}`, lightRows.answer, row?.action === `light.${state}` && isSmoke(row.actor));
  }
  if (written && released) {
    const autoRows = await activitySinceStart(bff, `target=${MAIN_AUTO}`);
    check(`API-17 lists the ${MAIN_AUTO} write and release by ${SMOKE_USER}`, autoRows.answer, bothWritesListed(autoRows.rows, written, released));
  } else if (written) {
    said(false, `API-17 lists the ${MAIN_AUTO} write and release by ${SMOKE_USER}`, "this check released nothing");
  }
}

// ── --agent-write: what the Job's two writes left in API-17 (#1144 step 3) ─

async function agentActivity(bff, name, param, from) {
  const answer = await bff(`/api/v1/activity?source=agent:${name}&target=${param}&from=${from}&limit=200`);
  const rows = answer.status === 200 && Array.isArray(answer.json?.items) ? answer.json.items : [];
  const byAgent = (row) => row?.actor?.source === `agent:${name}` && row?.actor?.name === name;
  const write = rows.find((row) => row.action === "setting.write");
  const freed = rows.find((row) => row.action === "setting.release");
  const ok = byAgent(write) && byAgent(freed) && rows.indexOf(freed) < rows.indexOf(write);
  check(`API-17 lists setting.write, then setting.release, source agent:${name}, actor ${name}`, answer, ok);
}

const legs = {
  baseline: () => withSession("viewer", baseline),
  sweep: () => withSession("viewer", sweep),
  parity: () => withSession("viewer", (bff) => parity(bff, Number(legArgs[0]))),
  "agent-activity": () => withSession("viewer", (bff) => agentActivity(bff, ...legArgs)),
  operator: () => withSession("operator", (bff) => withSession("viewer", (viewer) => operatorWrite(bff, viewer, legArgs[0]))),
  revert: () => withSession("operator", (bff) => revertLeg(bff, legArgs[0])),
  lights: () => withSession("operator", lights),
};

if (leg !== "parity") {
  const ready = await call(`${BFF}/readyz`);
  const revision = ready.json?.source_revision;
  const revisionOk = expectedSha ? revision === expectedSha : /^[0-9a-f]{40}$/.test(revision ?? "");
  check(`web /readyz is ready at ${expectedSha ? "the expected commit" : "a full commit SHA"}`, ready, ready.status === 200 && ready.json?.status === "ready" && revisionOk);
}
await legs[leg]();
console.log("END");
JS
}

# Inside a verdify-mcp pod, with the Iris token already in its environment as scripts/k3s-smoke.sh does: MCP
# irrigation_status at the epoch second $1, printed as one hash per field, like the web pod's parity read.
mcp_pod_read() {
  exec kubectl -n verdify-prod exec -i deploy/verdify-mcp -c mcp -- python - "$1" 2>/dev/null <<'PY'
import hashlib
import json
import os
import re
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/mcp"
PROTOCOL = "2025-11-25"
IGNORED_FIELD = re.compile(r"^(server_time|observed_at|age_s)$|_age_s$")


def rpc(ident, method, params, token):
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer " + token,
    }
    if method != "initialize":
        headers["mcp-protocol-version"] = PROTOCOL
    payload = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
    request = urllib.request.Request(URL, json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read()
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            return json.loads(raw)
    # The stream can carry notifications before the answer.
    messages = (json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: "))
    return next(message for message in messages if message.get("id") == ident)


def scalar_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return "num:" + (str(int(value)) if float(value).is_integer() and abs(value) < 1e15 else f"{value:.9f}")
    return "str:" + str(value)


def print_fields(value, path):
    if isinstance(value, list) and value:
        for index, item in enumerate(value):
            print_fields(item, f"{path}[{index}]")
    elif isinstance(value, dict) and value:
        for key, item in value.items():
            if not IGNORED_FIELD.search(key):
                print_fields(item, f"{path}.{key}")
    else:
        text = "[]" if isinstance(value, list) else "{}" if isinstance(value, dict) else scalar_text(value)
        print(f"FIELD {path} {hashlib.sha256(text.encode()).hexdigest()[:16]}")


try:
    token = os.environ["VERDIFY_MCP_TOKEN_IRIS"]
    client = {"name": "verdify-dashboard-smoke", "version": "1"}
    rpc(1, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": client}, token)
    time.sleep(max(0.0, float(sys.argv[1]) - time.time()))
    read_at = int(time.time() * 1000)
    result = rpc(2, "tools/call", {"name": "irrigation_status", "arguments": {}}, token)["result"]
    assert not result.get("isError"), "tool error"
    body = json.loads(result["content"][0]["text"])
    print(f"AT {read_at}")
    for line in body["lines"]:
        print_fields(line, line["line_id"])
    print_fields(body["program"], "program")
except Exception as exc:  # the host prints this line, so it names the type and nothing from the answer
    print(f"FAIL MCP irrigation_status did not answer ({type(exc).__name__})")
    sys.exit(1)
print("END")
PY
}

# The named agent's one-shot program, run by the Job as python -c. It holds no secret: the key arrives as an env var.
agent_job_program() {
  cat <<'PY'
import http.client
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.request
import uuid

URL = os.environ["MCP_URL"]
KEY = os.environ["MCP_AGENT_KEY"]
PARAM = os.environ["SMOKE_PARAM"]
SOURCE = "agent:" + os.environ["SMOKE_AGENT"]
REASON = "R2 live check"
PROTOCOL = "2025-11-25"
CONNECT_RETRY_S = 30
CALL_TIMEOUT_S = 15
TRIES = 3
# A release does not undo a crop-band write, and an irrigation-site write cannot confirm before the engine firmware.
NOT_CHECKABLE = {"crop_band", "irrigation_site"}
TRANSPORT = (OSError, http.client.HTTPException, ValueError, StopIteration)
next_id = iter(range(1, 100))


def say(verdict, words):
    print(f"{verdict} {words}", flush=True)


def rpc(method, params):
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer " + KEY,
    }
    if method != "initialize":
        headers["mcp-protocol-version"] = PROTOCOL
    ident = next(next_id)
    payload = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params}
    request = urllib.request.Request(URL, json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=CALL_TIMEOUT_S) as response:
        raw = response.read()
        if not response.headers.get("content-type", "").startswith("text/event-stream"):
            return json.loads(raw)
    # The stream can carry notifications before the answer.
    messages = (json.loads(line[6:]) for line in raw.splitlines() if line.startswith(b"data: "))
    return next(message for message in messages if message.get("id") == ident)


def tool(name, arguments):
    """(answered without isError, the JSON body). Only a body's state and code are ever printed."""
    answer = rpc("tools/call", {"name": name, "arguments": arguments})
    result = answer.get("result")
    if not isinstance(result, dict):
        return False, {}
    try:
        body = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, ValueError):
        body = {}
    return not result.get("isError"), body if isinstance(body, dict) else {}


def code_of(body):
    return re.sub(r"[^a-z0-9_]", "", str(body.get("code", "no_code")))[:40]


def state_of(body):
    return re.sub(r"[^a-z_]", "", str(body.get("state", "")))[:40]


def connect():
    """The CNI can refuse a brand-new pod for its first seconds, so the first connect retries for up to 30 s."""
    deadline = time.monotonic() + CONNECT_RETRY_S
    client = {"name": "verdify-dashboard-smoke-agent", "version": "1"}
    while True:
        try:
            rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": client})
            return True
        except urllib.error.HTTPError as refused:
            words = ": MCP admits a new key within 3 minutes, so repeat the run" if refused.code == 401 else ""
            say("FAIL", f"MCP refused the key ({refused.code}){words}")
            return False
        except TRANSPORT:
            if time.monotonic() >= deadline:
                say("FAIL", f"MCP did not answer within {CONNECT_RETRY_S} s")
                return False
            time.sleep(2)


def read_row():
    """The setting's row from the settings tool, tried like a write: (True, row or None), or (False, None)."""
    for attempt in range(TRIES):
        try:
            ok, body = tool("settings", {"param": PARAM})
            if ok:
                return True, next((item for item in body.get("items", []) if item.get("param") == PARAM), None)
        except urllib.error.HTTPError as refused:
            if refused.code < 500:
                return False, None
        except TRANSPORT:
            pass
        if attempt + 1 < TRIES:
            time.sleep(2)
    return False, None


def why_not_checkable(row):
    if not row:
        return "not in the settings tool"
    if not row.get("writable"):
        return "not writable"
    if row.get("kind") != "numeric" or row.get("control_class") in NOT_CHECKABLE:
        return "one step of it cannot be undone or confirmed"
    desired = row.get("desired")
    if desired and desired.get("value") is not None:
        return "its newest desired row is neither absent nor released, so it stays"
    if not isinstance((row.get("readback") or {}).get("value"), (int, float)):
        return "no device value"
    if not (row.get("step") or 0) > 0:
        return "no step"
    return None


def one_step(row):
    """The device value plus one grid step, minus at the max. The server snaps it again."""
    device, step, top = row["readback"]["value"], row["step"], row.get("max")
    up = round(round((device + step) / step) * step, 9)
    return up if top is None or up <= top else round(round((device - step) / step) * step, 9)


def with_tries(arguments):
    """set_setting, with the same request_id after a lost answer: (ok, body), or (None, {}) with no answer at all."""
    for attempt in range(TRIES):
        try:
            return tool("set_setting", arguments)
        except urllib.error.HTTPError as refused:
            if refused.code < 500:
                return False, {"code": f"http_{refused.code}"}
        except TRANSPORT:
            pass
        if attempt + 1 < TRIES:
            time.sleep(2)
    return None, {}


def release():
    """An agent's release takes any row, a person's included, so another writer's newest row stays and a FAIL says so."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    answered, row = read_row()
    if not answered:
        say("FAIL", f"the settings tool did not answer, so nothing was released: read {PARAM} on Settings")
        return False
    newest = (row or {}).get("desired") or {}
    if newest.get("value") is not None and (newest.get("actor") or {}).get("source") != SOURCE:
        say("FAIL", f"{PARAM}'s newest desired row is another writer's, so it stays and this check releases nothing")
        return False
    ok, body = with_tries({"param": PARAM, "release": True, "reason": REASON, "request_id": str(uuid.uuid4())})
    if ok and body.get("state") == "released":
        say("PASS", "set_setting(release=true) answers state released")
        return True
    say("FAIL", f"set_setting(release=true) answered {'no state' if ok else code_of(body)}: release {PARAM} on Settings")
    return False


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    if not connect():
        return 1
    answered, row = read_row()
    if not answered:
        say("FAIL", "the settings tool did not answer")
        return 1
    why = why_not_checkable(row)
    if why:
        say("FAIL", f"{PARAM} refused: {why}")
        return 1
    say("PASS", f"{PARAM} can take the check: its newest desired row is absent or released")
    # From here the host reads API-17, and the release runs unless the write was refused: a lost answer may have stored it.
    print("WRITING", flush=True)
    stored = True
    failed = False
    try:
        arguments = {"param": PARAM, "value": one_step(row), "reason": REASON, "request_id": str(uuid.uuid4())}
        ok, body = with_tries(arguments)
        stored = ok is not False
        if ok and state_of(body):
            say("PASS", f"set_setting answers state {state_of(body)}")
        else:
            failed = True
            say("FAIL", f"set_setting answered {'no state' if ok else code_of(body)}")
    finally:
        if stored and not release():
            failed = True
    return 1 if failed else 0


sys.exit(main())
PY
}

# The one-shot Job of #1144 step 3. $1 is the live verdify-mcp image. The label is what allow-mcp-from-named-agents
# admits; the Job has no ServiceAccount token and reads only this agent's key.
agent_job_manifest() {
  cat <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: ${AGENT_JOB}
  namespace: verdify-prod
  labels:
    app.kubernetes.io/part-of: verdify
    app.kubernetes.io/component: dashboard-smoke
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 120
  ttlSecondsAfterFinished: 600
  template:
    metadata:
      labels:
        app.kubernetes.io/part-of: verdify
        app.kubernetes.io/component: dashboard-smoke
        verdify.ai/mcp-agent: "true"
    spec:
      restartPolicy: Never
      automountServiceAccountToken: false
      imagePullSecrets:
        - name: zot-origin-cluster-pull
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: agent-write
          image: ${1}
          command: ["python", "-c"]
          args:
            - |
$(agent_job_program | sed 's/^/              /')
          env:
            - name: MCP_URL
              value: http://verdify-mcp.verdify-prod.svc:8000/mcp
            - name: SMOKE_PARAM
              value: "${PARAM}"
            - name: SMOKE_AGENT
              value: "${AGENT}"
            - name: MCP_AGENT_KEY
              valueFrom:
                secretKeyRef:
                  name: verdify-mcp-agents
                  key: "${AGENT}"
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              memory: 128Mi
YAML
}

# Kill "$@" after $1 seconds: kubectl's request timeout does not bound a streaming exec. The in-pod
# program still ends on its own deadlines, so a killed exec does not stop its release or its INT-02.
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
      "HELD "*) held "${line#HELD }" ;;
      END) finished=1 ;;
    esac
  done < <(bounded "${WEB_POD_DEADLINE_S}" web_pod_checks "$@")
  if [ "${finished}" -ne 1 ]; then
    fail "the checks in the web pod did not finish (no pod, a failed exec or a crash)"
    case "${1:-}" in
      operator|revert) fail "${PARAM} may still hold dashboard-smoke's row: see Live checks in docs/runbooks/dashboard.md" ;;
      lights) fail "the main light and sw_gl_main_auto_mode may still be off their values: see Live checks in docs/runbooks/dashboard.md" ;;
    esac
  fi
}

# One side's fields, FIELD <path> <hash>, as "<path> <hash>" sorted for comm.
parity_fields() {
  awk '$1 == "FIELD" { print $2, $3 }' "$1" | LC_ALL=C sort
}

# Each pod reads at the same epoch second and prints only hashes, so the comparison here never holds a value.
run_parity() {
  local attempt=1 at web_file mcp_file web_pid mcp_pid differing count web_at mcp_at gap side file line stopped
  WORK_DIR="$(mktemp -d)"
  web_file="${WORK_DIR}/web"
  mcp_file="${WORK_DIR}/mcp"
  while [ "${attempt}" -le "${PARITY_ATTEMPTS}" ]; do
    at=$(( $(date +%s) + PARITY_LEAD_S ))
    bounded "${PARITY_DEADLINE_S}" web_pod_checks parity "${at}" > "${web_file}" 2>/dev/null &
    web_pid=$!
    bounded "${PARITY_DEADLINE_S}" mcp_pod_read "${at}" > "${mcp_file}" 2>/dev/null &
    mcp_pid=$!
    wait "${web_pid}" 2>/dev/null
    wait "${mcp_pid}" 2>/dev/null
    stopped=0
    for side in web mcp; do
      file="${WORK_DIR}/${side}"
      while IFS= read -r line; do
        case "${line}" in
          "FAIL "*) fail "${line#FAIL }"; stopped=1 ;;
        esac
      done < "${file}"
      if ! grep -qx END "${file}"; then
        fail "parity: the ${side} read did not finish"
        stopped=1
      fi
    done
    if [ "${stopped}" -eq 1 ]; then return; fi
    count="$(parity_fields "${web_file}" | wc -l | tr -d ' ')"
    differing="$(LC_ALL=C comm -3 <(parity_fields "${web_file}") <(parity_fields "${mcp_file}") | awk '{ print $1 }' | LC_ALL=C sort -u)"
    if [ -z "${differing}" ] && [ "${count}" -gt 0 ]; then
      web_at="$(awk '$1 == "AT" { print $2; exit }' "${web_file}")"
      mcp_at="$(awk '$1 == "AT" { print $2; exit }' "${mcp_file}")"
      gap=$(( ${web_at:-0} - ${mcp_at:-0} ))
      pass "MCP irrigation_status and API-IRR-01: equal (${count} fields, reads ${gap#-} ms apart, read ${attempt} of at most ${PARITY_ATTEMPTS})"
      return
    fi
    attempt=$((attempt+1))
  done
  if [ -z "${differing}" ]; then
    fail "MCP irrigation_status and API-IRR-01: no fields to compare"
  else
    fail "MCP irrigation_status and API-IRR-01 differ: $(head -n 40 <<< "${differing}" | tr '\n' ' ')"
  fi
}

# --agent-write. Nothing reaches the cluster until the key is known to exist, and only its name is read.
run_agent_leg() {
  local secret_names image conditions started_at logs line writing=0 waited=0 ended=0
  if ! secret_names="$(kubectl -n verdify-prod describe secret verdify-mcp-agents 2>/dev/null)"; then
    fail "agent key: Secret verdify-prod/verdify-mcp-agents is absent or unreadable, so no Job ran and nothing was written (jvallery/agents#6017)"
    return
  fi
  if ! awk -v key="${AGENT}:" 'seen && $1 == key { found = 1 } /^====/ { seen = 1 } END { exit !found }' <<< "${secret_names}"; then
    fail "agent key: Secret verdify-prod/verdify-mcp-agents has no key ${AGENT}, so no Job ran and nothing was written: Jason seals it in jvallery/agents (#6017)"
    return
  fi
  pass "agent key ${AGENT} is in Secret verdify-prod/verdify-mcp-agents"
  image="$(kubectl -n verdify-prod get deploy verdify-mcp -o jsonpath='{.spec.template.spec.containers[?(@.name=="mcp")].image}' 2>/dev/null)"
  case "${image}" in
    *@sha256:*) ;;
    *) fail "the verdify-mcp Deployment names no image digest, so no Job ran and nothing was written"; return ;;
  esac
  AGENT_JOB="dashboard-smoke-agent-$(date +%s)"
  if ! agent_job_manifest "${image}" | kubectl -n verdify-prod create -f - >/dev/null 2>&1; then
    fail "could not create the agent Job, so nothing was written"
    AGENT_JOB=""
    return
  fi
  while [ "${waited}" -lt "${AGENT_JOB_WAIT_S}" ]; do
    conditions="$(kubectl -n verdify-prod get job "${AGENT_JOB}" -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null)"
    case " ${conditions} " in
      *" Complete "*|*" Failed "*) ended=1; break ;;
    esac
    sleep 3
    waited=$((waited+3))
  done
  started_at="$(kubectl -n verdify-prod get job "${AGENT_JOB}" -o jsonpath='{.status.startTime}' 2>/dev/null)"
  logs="$(kubectl -n verdify-prod logs "job/${AGENT_JOB}" 2>/dev/null)"
  while IFS= read -r line; do
    case "${line}" in
      "PASS "*) pass "agent Job: ${line#PASS }" ;;
      "FAIL "*) fail "agent Job: ${line#FAIL }" ;;
      WRITING) writing=1 ;;
    esac
  done <<< "${logs}"
  if [ "${ended}" -ne 1 ]; then
    fail "the agent Job did not end within ${AGENT_JOB_WAIT_S} s"
  elif [[ " ${conditions} " == *" Failed "* ]]; then
    fail "the agent Job ended Failed: if it got as far as writing, read ${PARAM} on Settings"
  fi
  if kubectl -n verdify-prod delete job "${AGENT_JOB}" --wait=false >/dev/null 2>&1; then
    pass "the agent Job is deleted"
    AGENT_JOB=""
  else
    fail "could not delete the agent Job ${AGENT_JOB}; its ttlSecondsAfterFinished removes it"
  fi
  if [ "${writing}" -ne 1 ]; then
    # A Job ends Complete only after its write and release, so a log that does not say so shows nothing was checked.
    if [ "${ended}" -eq 1 ] && [[ " ${conditions} " != *" Failed "* ]]; then
      fail "the agent Job ended Complete but its log shows no write, so nothing was checked"
    fi
    return
  fi
  if ! [[ "${started_at}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z$ ]]; then
    fail "the agent Job has no start time, so API-17 could not be read"
    return
  fi
  run_web_pod_checks agent-activity "${AGENT}" "${PARAM}" "${started_at}"
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

echo "=== dashboard smoke: ${LEG} ($(date '+%Y-%m-%d %H:%M:%S')) ==="
case "${LEG}" in
  baseline)
    echo "[1] inside the web pod"
    run_web_pod_checks
    echo ""
    echo "[2] from this host"
    run_host_checks
    ;;
  sweep)
    echo "[1] every served read through the BFF, as the viewer"
    run_web_pod_checks sweep
    echo ""
    echo "[2] MCP irrigation_status against API-IRR-01"
    run_parity
    ;;
  agent)
    echo "[1] set_setting and its release from the labelled Job, as agent ${AGENT}, on ${PARAM}"
    run_agent_leg
    ;;
  operator)
    echo "[1] one grid step on ${PARAM} through the BFF, as the operator, released at the end"
    run_web_pod_checks operator "${PARAM}"
    ;;
  revert)
    echo "[1] two writes and a Revert on ${PARAM} through the BFF, as the operator, released at the end"
    run_web_pod_checks revert "${PARAM}"
    ;;
  lights)
    echo "[1] the main light once each way through the BFF, as the operator, ending at the controller's intent"
    run_web_pod_checks lights
    ;;
esac
echo ""
echo "── summary ─────────────────────────────────"
echo "  passed=${PASS} failed=${FAIL}"
if [ "${FAIL}" -gt 0 ]; then
  echo "  RESULT: NOT GREEN — review the FAIL and HELD lines above."
  exit 1
fi
echo "  RESULT: GREEN"
exit 0
