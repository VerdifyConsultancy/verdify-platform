// SPDX-License-Identifier: MIT
/**
 * The fixture API's behaviour (VD-02): writes as the contract says, idempotency, roles and the writes flag, the
 * engine-less greenhouse, the request log and what each named fixture serves. No database, MCP, Home Assistant or
 * device is involved; every time is relative to a pinned clock at 10:42 America/Denver.
 */
import { readFileSync } from "node:fs";
import request from "supertest";
import { expect, test } from "vitest";
import { createFixtureHandler, startFixtureServer } from "../../e2e/fixture-api.mjs";
import { fixture } from "../../e2e/fixtures/index.mjs";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const KEY = "5f0c6a52-8e1d-4c3b-9a7f-2d4e6b8c0a1e";
const OTHER_KEY = "9b2d4f6a-1c3e-4a5b-8d7f-0e2c4a6b8d1f";
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
type Body = Record<string, unknown>;
type Api = ReturnType<typeof createFixtureHandler>;
const at = (api: Api, method: string, url: string, body?: unknown, key?: string) => api.respond({ method, url, body, headers: key ? { "Idempotency-Key": key } : {} });
const get = (api: Api, url: string) => api.respond({ url }).body;
const setting = (api: Api, param: string) => (get(api, "/v1/settings").items as Body[]).find((row) => row.param === param)!;
const line = (api: Api, id: string) => (get(api, "/v1/irrigation").lines as Body[]).find((row) => row.line_id === id)!;
const handler = (fixtureName: string | string[] = "in-band", options: Body = {}) => createFixtureHandler({ fixture: fixtureName, clock: () => NOW, ...options });

test("API-10 snaps 1.53 to 1.55 and refuses 2.60 beside the field", () => {
  const api = handler();
  const saved = at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.53, manual: true, reason: "Vandas drying" }, KEY);
  expect(saved.status).toBe(200);
  expect(saved.body).toMatchObject({ param: "mister_engage_kpa", value: 1.55, snapped_from: 1.53, manual: true, manual_before: false, state: "requested", actor: { source: "dashboard", name: "jason" } });
  expect(setting(api, "mister_engage_kpa")).toMatchObject({ applied_from: "manual", state: "requested", desired: { value: 1.55 } });
  const refused = at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 2.6 }, OTHER_KEY);
  expect(refused.status).toBe(422);
  expect(refused.headers["Content-Type"]).toBe("application/problem+json");
  expect(refused.body).toMatchObject({ code: "out_of_range", field: "value", min: 0.5, max: 2.5, current: 1.6, retryable: false, detail: "Must be 0.50-2.50 kPa" });
  expect(at(api, "PUT", "/v1/settings/temp_low", { value: 45 }).body).toMatchObject({ status: 422, code: "read_only" });
  expect(at(api, "PUT", "/v1/settings/no_such_param", { value: 1 }).body).toMatchObject({ status: 422, code: "unknown_target" });
});

test("a replayed Idempotency-Key returns the stored body once; a reused key with another body is refused", () => {
  const api = handler();
  const first = at(api, "POST", "/v1/irrigation/lines/L01/run", { minutes: 12 }, KEY);
  const replay = at(api, "POST", "/v1/irrigation/lines/L01/run", { minutes: 12 }, KEY);
  expect(first.status).toBe(202);
  expect(replay.status).toBe(202);
  expect(replay.body).toEqual(first.body);
  expect(replay.headers["Idempotency-Replayed"]).toBe("true");
  expect(first.headers["Idempotency-Replayed"]).toBeUndefined();
  // Applied once: one run, one activity row (a second real run would be line_busy).
  expect((get(api, "/v1/activity?action=irrigation.run").items as Body[]).filter((row) => row.request_id === KEY)).toHaveLength(1);
  // The same payload with its keys in another order is the same request.
  const saved = at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.55, manual: true }, OTHER_KEY);
  expect(at(api, "PUT", "/v1/settings/mister_engage_kpa", JSON.stringify({ manual: true, value: 1.55 }), OTHER_KEY)).toMatchObject({ body: saved.body, headers: { "Idempotency-Replayed": "true" } });
  const reused = at(api, "POST", "/v1/irrigation/lines/L01/run", { minutes: 13 }, KEY);
  expect(reused.status).toBe(422);
  expect(reused.body).toMatchObject({ code: "request_id_reused", request_id: KEY });
  expect(at(api, "POST", "/v1/irrigation/stop", {}, "not-a-uuid").body).toMatchObject({ status: 400, code: "invalid_request" });
});

test("GET /__requests lists every request since the last /__control with its key, and /__control resets it", () => {
  const api = handler();
  get(api, "/v1/now");
  at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.55, manual: true }, KEY);
  at(api, "POST", "/v1/irrigation/stop", {}, OTHER_KEY);
  get(api, "/api/v1/me");
  expect(get(api, "/__requests")).toEqual({ requests: [
    { method: "GET", path: "/v1/now", operationId: "API-02", idempotency_key: null, body: null },
    { method: "PUT", path: "/v1/settings/mister_engage_kpa", operationId: "API-10", idempotency_key: KEY, body: { value: 1.55, manual: true } },
    { method: "POST", path: "/v1/irrigation/stop", operationId: "API-IRR-07", idempotency_key: OTHER_KEY, body: {} },
    { method: "GET", path: "/api/v1/me", operationId: "API-01", idempotency_key: null, body: null },
  ] });
  at(api, "POST", "/__control", { fixture: "in-band" });
  expect(get(api, "/__requests")).toEqual({ requests: [] });
  // The write is gone with the reset: the setting reads as the base again.
  expect(setting(api, "mister_engage_kpa")).toMatchObject({ desired: null, applied_from: "plan" });
});

test("a viewer's every write is 403; with writes off every write but Stop is 503", () => {
  const viewer = handler("viewer-session");
  for (const [method, url, body] of [["PUT", "/v1/settings/mister_engage_kpa", { value: 1.6 }], ["POST", "/v1/irrigation/stop", {}], ["POST", "/v1/equipment/override", { kind: "clear" }]] as const) {
    expect(at(viewer, method, url, body).body, url).toMatchObject({ status: 403, code: "forbidden" });
  }
  expect(get(viewer, "/v1/me")).toMatchObject({ role: "viewer" });
  const off = handler("writes-off");
  expect(at(off, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.6 }).body).toMatchObject({ status: 503, code: "writes_disabled" });
  expect(at(off, "POST", "/v1/irrigation/lines/L01/run", { minutes: 12 }).body).toMatchObject({ status: 503, code: "writes_disabled" });
  expect(at(off, "POST", "/v1/irrigation/stop", { line_id: "L01" }).status).toBe(202);
  expect(get(off, "/v1/me")).toMatchObject({ writes_enabled: false });
});

test("before the engine: run on L04 is 503 with the firmware words, L01 runs, Stop works for a line and for all", () => {
  const api = handler("before-engine");
  expect(get(api, "/v1/irrigation")).toMatchObject({ engine: false, program: { src: null, reason: "engine not on controller" } });
  const l04 = at(api, "POST", "/v1/irrigation/lines/L04/run", { on_s: 45, count: 4, gap_s: 60 }, KEY);
  expect(l04.status).toBe(503);
  expect(l04.body).toMatchObject({ code: "unavailable", retryable: false, detail: "arrives with the irrigation firmware" });
  expect(at(api, "POST", "/v1/irrigation/lines/L02/run", { minutes: 5 }).body).toMatchObject({ status: 503, retryable: false });
  // L01 runs for the controller's configured duration only.
  expect(at(api, "POST", "/v1/irrigation/lines/L01/run", { minutes: 30 }).body).toMatchObject({ status: 422, code: "out_of_range", field: "minutes", min: 12, max: 12 });
  expect(at(api, "POST", "/v1/irrigation/lines/L01/run", {}).status).toBe(202);
  expect(line(api, "L01")).toMatchObject({ state: { code: "running", cause: "run_now" } });
  expect(at(api, "POST", "/v1/irrigation/stop", { line_id: "L01" })).toMatchObject({ status: 202, body: { kind: "stop", line_id: "L01", status: "pending" } });
  expect(at(api, "POST", "/v1/irrigation/stop", {})).toMatchObject({ status: 202, body: { kind: "stop", line_id: null } });
  expect(line(api, "L01")).toMatchObject({ state: { code: "idle" } });
  expect(at(api, "POST", "/v1/equipment/override", { kind: "fans", minutes: 15 }).body).toMatchObject({ status: 503, retryable: false });
  expect(at(api, "POST", "/v1/irrigation/clear-fault", {}).body).toMatchObject({ status: 503, retryable: false });
  // A saved site cap waits for the engine.
  const saved = at(api, "PUT", "/v1/settings/mister_daily_volume_max_gal", { value: 300, manual: true });
  expect(saved.body).toMatchObject({ state: "requested", state_reason: { code: "no_engine", since: saved.body.created_at } });
  expect(setting(api, "mister_daily_volume_max_gal")).toMatchObject({ state: "requested", desired: { state_reason: { code: "no_engine" } } });
  const schedules = get(api, "/v1/irrigation/schedules").occurrences as Body[];
  expect(schedules.every((row) => row.status === "blocked" && row.reason === "arrives with the irrigation firmware")).toBe(true);
});

test("after API-IRR-07 every running line reads OFF from the next API-IRR-01 read", () => {
  const api = handler("l01-running");
  expect(line(api, "L01")).toMatchObject({ state: { code: "running", cause: "run_now", observed_at: minutesAgo(12) } });
  expect(get(api, "/v1/irrigation").active_runs).toEqual([expect.objectContaining({ manifold: "drip", line_id: "L01", cause: "run_now" })]);
  expect(get(api, "/v1/now").running).toEqual([expect.objectContaining({ equipment: "drip_wall", line_id: "L01", cause: "run_now" })]);
  expect(at(api, "POST", "/v1/irrigation/stop", {}).status).toBe(202);
  expect(line(api, "L01").state).toMatchObject({ code: "idle" });
  expect(get(api, "/v1/irrigation").active_runs).toEqual([]);
  expect(get(api, "/v1/now").running).toEqual([]);
  expect((get(api, "/v1/equipment").items as Body[]).find((item) => item.equipment === "drip_wall")).toMatchObject({ state: "off" });
});

test("stop-unconfirmed: a running L01 stays in API-02 running and API-IRR-01 after API-IRR-07, alone or after l01-running", () => {
  for (const names of ["stop-unconfirmed", ["l01-running", "stop-unconfirmed"]]) {
    const api = handler(names);
    expect(get(api, "/v1/now").running).toEqual([expect.objectContaining({ equipment: "drip_wall", line_id: "L01", cause: "run_now" })]);
    expect(at(api, "POST", "/v1/irrigation/stop", {}, KEY).status).toBe(202);
    expect(get(api, "/v1/now").running).toEqual([expect.objectContaining({ line_id: "L01" })]);
    expect(line(api, "L01").state).toMatchObject({ code: "running" });
    expect(get(api, "/api/v1/irrigation").active_runs).toEqual([expect.objectContaining({ line_id: "L01" })]);
    expect((get(api, "/v1/activity?action=irrigation.stop").items as Body[])[0]).toMatchObject({ target: "all", detail: { stopped: [] }, request_id: KEY });
  }
  // fixture() answers the reads after its Stop.
  expect(fixture("stop-unconfirmed", { now: NOW })["API-02"].running).toEqual([expect.objectContaining({ line_id: "L01" })]);
});

test("requested-to-confirmed: two API-09 reads requested, then confirmed; with confirm false, not_confirmed since the send", () => {
  for (const [options, settled] of [[{}, { state: "confirmed", state_reason: null }], [{ confirm: false }, { state: "not_confirmed" }]] as const) {
    const api = handler("requested-to-confirmed", options);
    const saved = at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.55, manual: true }, KEY).body;
    const reads = [1, 2, 3, 4].map(() => setting(api, "mister_engage_kpa"));
    expect(reads.map((row) => row.state)).toEqual(["requested", "requested", settled.state, settled.state]);
    expect(reads[2].desired).toMatchObject(settled);
    if (settled.state === "not_confirmed") expect(reads[2].desired).toMatchObject({ state_reason: { code: "no_match", since: saved.created_at } });
    else expect(reads[2].readback).toMatchObject({ value: 1.55 });
  }
  // Selecting it from Playwright does the same.
  const api = handler();
  at(api, "POST", "/__control", { fixture: "requested-to-confirmed", confirm: false });
  at(api, "PUT", "/v1/settings/mister_engage_kpa", { value: 1.55, manual: true });
  expect([1, 2, 3].map(() => setting(api, "mister_engage_kpa").state)).toEqual(["requested", "requested", "not_confirmed"]);
});

test("fail makes single operations answer a status; api-down fails every /v1 operation", () => {
  const api = handler("in-band", { fail: { "API-14": 503, "API-02": 401 } });
  expect(at(api, "GET", "/v1/alerts").body).toMatchObject({ status: 503, code: "unavailable" });
  expect(at(api, "GET", "/v1/now").body).toMatchObject({ status: 401, code: "unauthenticated" });
  expect(at(api, "GET", "/v1/me").status).toBe(200);
  expect(at(api, "POST", "/__control", { fixture: "in-band", fail: { "API-99": 503 } }).status).toBe(400);
  const down = handler("api-down");
  for (const url of ["/v1/me", "/v1/now", "/v1/irrigation"]) expect(at(down, "GET", url).body, url).toMatchObject({ status: 503, code: "unavailable", retryable: true });
  expect(at(down, "POST", "/v1/irrigation/stop", {}).status).toBe(503);
  expect(at(down, "GET", "/healthz").body).toEqual({ status: "ok" });
});

test("times are relative to the request, so ages read the same in every run", () => {
  for (const clock of [NOW, NOW + 3_600_000]) {
    const api = createFixtureHandler({ clock: () => clock });
    const now = get(api, "/v1/now");
    expect(Date.parse(now.server_time) - Date.parse(now.indoor.temp_f.observed_at)).toBe(20_000);
    expect(Date.parse(now.server_time) - Date.parse(now.band.observed_at)).toBe(240_000);
  }
});

test("the base, in-band, is the screens' SCR-01 greenhouse", () => {
  const set = fixture("in-band", { now: NOW });
  expect(set["API-01"]).toMatchObject({ username: "jason", role: "operator", writes_enabled: true });
  expect(set["API-02"]).toMatchObject({
    indoor: { temp_f: { value: 64.8, observed_at: minutesAgo(1 / 3) }, vpd_kpa: { value: 0.62 }, rh_pct: { value: 71 } },
    band: { temp_f: { low: 62, high: 70, verdict: "in" }, observed_at: minutesAgo(4) }, alerts: { open: 0 }, running: [], overrides: [],
    leak: { active: false, alert_id: null }, freshness: { ingestor: { stale: false }, climate: { stale: false } },
  });
  expect(set["API-14"]).toEqual({ next_cursor: null, items: [] });
  expect(set["API-IRR-01"]).toMatchObject({ engine: true, program: { revision: 42, state: "confirmed", src: "nvs" }, active_runs: [] });
  expect(set["API-IRR-01"].lines.filter((row: Body) => row.reserved)).toEqual([expect.objectContaining({ line_id: "L05", enabled: false }), expect.objectContaining({ line_id: "L06", enabled: false })]);
  const caps = set["API-09"].items.filter((row: Body) => row.group === "site_water_caps");
  expect(caps.map((row: Body) => [row.param, (row.readback as Body).value, row.state])).toEqual([["mister_daily_volume_max_gal", 360, "confirmed"], ["sched_mist_rh_max_pct", 90, "confirmed"], ["climate_yield_min", 15, "confirmed"]]);
  const health = set["API-07"].health as Body[];
  expect(health.every((row) => typeof row.expected_at === "string" && typeof row.due_at === "string")).toBe(true);
  expect(health.filter((row) => row.outcome === "expected" && Date.parse(row.expected_at as string) > NOW).length).toBeGreaterThan(0);
  expect(set["API-19"]).toMatchObject({ param: "band_vpd_low_sm", step_s: 600, unit: "kPa" });
  expect(set["API-19"].t).toHaveLength(144);
  expect(set["API-20"]).toMatchObject({ light: "main", state: "on", result: "confirmed" });
  expect(set["API-21"].days).toHaveLength(30);
  expect(set["API-21"].days.every((day: Body) => day.meter_gal === null || Number.isInteger(day.meter_gal))).toBe(true);
});

test("every equipment label the fixture serves is the API's: EQUIPMENT_LABELS in verdify_services/labels.py", () => {
  const source = readFileSync(new URL("../../../verdify_services/labels.py", import.meta.url), "utf8");
  const block = /EQUIPMENT_LABELS[^{]*\{([^}]*)\}/.exec(source)![1];
  const labels = Object.fromEntries([...block.matchAll(/"([a-z0-9_]+)":\s*"([^"]+)"/g)].map((match) => [match[1], match[2]]));
  expect(Object.keys(labels)).toHaveLength(21);
  const set = fixture("in-band", { now: NOW });
  const served = [...set["API-13"].items, ...set["API-05"].lanes].map((item: Body) => [item.equipment, item.label]);
  expect(served.length).toBe(18 + 21);
  for (const [equipment, label] of served) expect(label, String(equipment)).toBe(labels[String(equipment)]);
});

test("each named fixture serves what the table says", () => {
  const set = (name: string | string[]) => fixture(name, { now: NOW });
  expect(set("stale-data")["API-02"]).toMatchObject({ indoor: { temp_f: { observed_at: minutesAgo(14) } }, freshness: { ingestor: { stale: false }, climate: { stale: false } } });
  expect(set("ingestor-not-running")["API-02"].freshness).toMatchObject({ ingestor: { age_s: 240, stale: true }, climate: { age_s: 240, stale: true } });
  const quiet = set("controller-not-reporting");
  expect(quiet["API-02"]).toMatchObject({ freshness: { ingestor: { age_s: 30, stale: false }, climate: { age_s: 360, stale: true } }, alerts: { open: 0 } });
  // As API-13 serves it (VD-54): every state unknown with its age, no since, runtime or cycles, and the intent unknown.
  expect(quiet["API-13"].items.every((item: Body) => item.state === "unknown" && typeof item.observed_at === "string")).toBe(true);
  expect(quiet["API-13"].items.every((item: Body) => item.since === null && item.runtime_today_s === null && item.cycles_today === null)).toBe(true);
  expect(quiet["API-13"].items.filter((item: Body) => item.group === "light").map((item: Body) => [item.intent, item.observed_at])).toEqual([["unknown", minutesAgo(12)], ["unknown", minutesAgo(12)]]);
  const merged = set("alerts-merged");
  const rows = merged["API-14"].items as Body[];
  expect(rows.some((row) => (row.count as number) > 1)).toBe(true);
  expect(rows.filter((row) => row.status === "acknowledged")).toHaveLength(1);
  expect(merged["API-02"].alerts.open).toBe(rows.filter((row) => row.status === "open").length);
  const flow = set("flow-valves-closed");
  expect(flow["API-02"].leak).toMatchObject({ active: true, alert_id: 201, gal_last_10min: 3, stopped_at: null });
  expect(flow["API-14"].items).toEqual([expect.objectContaining({ id: 201, alert_type: "leak_detected", status: "open" })]);
  expect(flow["API-IRR-01"].active_runs).toEqual([]);
  expect(set("flow-stopped")["API-IRR-01"].leak).toMatchObject({ active: false, alert_id: 201, stopped_at: minutesAgo(2) });
  const fault = set("mister-fault")["API-IRR-01"].lines.find((row: Body) => row.line_id === "L04");
  expect(fault).toMatchObject({ fault: "mister_fault", fault_until: "2026-11-03T07:00:00.000Z" });
  expect(set("mister-fault")["API-14"].items).toEqual([expect.objectContaining({ alert_type: "irrigation_line_faulted", status: "open" })]);
  const lane = set("superseded-lane")["API-07"].lanes.find((row: Body) => row.param === "mister_engage_kpa");
  expect(lane).toMatchObject({ applied_from: "desired", desired: { value: 1.55, manual: false, created_at: "2026-11-02T15:00:00.000Z" }, waypoints: [{ at: "2026-11-02T20:00:00.000Z", value: 1.7, effective: false }] });
  expect(set("superseded-lane")["API-07"].plan).toMatchObject({ written_at: "2026-11-02T14:02:00.000Z", plan_id: "iris-20261102-0702" });
  const missed = set("missed-sunset");
  expect(missed["API-02"].iris.required_trigger).toBe("missed");
  expect(missed["API-07"].health.find((row: Body) => row.outcome === "missed")).toMatchObject({ trigger: "SUNSET", required: true });
  expect(Date.parse(missed["API-07"].health.find((row: Body) => row.outcome === "missed").due_at)).toBeLessThan(NOW);
  const lights = set("grow-lights-differ")["API-13"].items.find((item: Body) => item.equipment === "grow_light_grow");
  expect(lights).toMatchObject({ state: "off", intent: "on" });
  // The base's Lutron rows are one Home Assistant poll old: known. Down, they are 11 min old: unknown, the intent kept.
  expect(set("in-band")["API-13"].items.filter((item: Body) => item.group === "light").map((item: Body) => [item.state, item.observed_at])).toEqual([["off", minutesAgo(3)], ["off", minutesAgo(3)]]);
  expect(set("ha-down")["API-13"].items.filter((item: Body) => item.group === "light")).toEqual([
    expect.objectContaining({ state: "unknown", intent: "off", since: null, runtime_today_s: null, observed_at: minutesAgo(11) }),
    expect.objectContaining({ state: "unknown", intent: "off", since: null, runtime_today_s: null, observed_at: minutesAgo(11) }),
  ]);
  // Fan 1 has been off since an absolute time; lanes-unknown adds one unknown span to its lane, cut out of the off.
  expect(set("in-band")["API-13"].items[0]).toMatchObject({ equipment: "fan1", state: "off", since: "2026-10-27T10:26:00.000Z" });
  const lanes = (name: string) => (set(name)["API-05"].lanes as Body[]).filter((lane) => (lane.intervals as Body[]).some((span) => span.state === "unknown"));
  expect(lanes("in-band")).toEqual([]);
  expect(lanes("lanes-unknown")).toEqual([expect.objectContaining({
    equipment: "fan1", label: "Fan 1",
    intervals: [
      { start: minutesAgo(24 * 60), end: "2026-11-02T09:10:00.000Z", state: "off", cause: null },
      { start: "2026-11-02T09:10:00.000Z", end: "2026-11-02T09:40:00.000Z", state: "unknown", cause: null },
      { start: "2026-11-02T09:40:00.000Z", end: minutesAgo(0), state: "off", cause: null },
    ],
  })]);
  // before-engine serves a site cap saved before the engine, requested with no_engine since that write.
  const engineless = set("before-engine");
  expect(engineless["API-09"].items.find((row: Body) => row.param === "mister_daily_volume_max_gal")).toMatchObject({
    state: "requested", readback: null, desired: { value: 300, manual: true, state: "requested", state_reason: { code: "no_engine", since: "2026-11-02T16:30:00.000Z" }, created_at: "2026-11-02T16:30:00.000Z" },
  });
  expect(engineless["API-17"].items).toEqual(expect.arrayContaining([expect.objectContaining({ action: "setting.write", target: "mister_daily_volume_max_gal", state: "requested" })]));
  // program-rev-42 is the base's program even after before-engine: the site caps and every line confirmed inside it.
  const restored = set(["before-engine", "program-rev-42"]);
  expect(restored["API-IRR-01"].program).toMatchObject({ revision: 42, state: "confirmed" });
  expect(restored["API-IRR-01"].lines.every((row: Body) => row.sync === "confirmed")).toBe(true);
  expect(restored["API-09"].items.filter((row: Body) => row.group === "site_water_caps").map((row: Body) => [row.state, (row.readback as Body).value, row.desired]))
    .toEqual([["confirmed", 360, null], ["confirmed", 90, null], ["confirmed", 15, null]]);
  expect(set("reserved-lines")["API-IRR-01"].lines.filter((row: Body) => row.reserved).map((row: Body) => row.line_id)).toEqual(["L05", "L06"]);
  // schedules-paused: the week with "Last 7 days" holds the past L02 block, skipped because the line was paused.
  const week = set("schedules-paused")["API-IRR-02"];
  expect(week.schedules).toHaveLength(3);
  expect((week.occurrences as Body[]).filter((row) => row.line_id === "L02" && Date.parse(row.planned_start as string) < NOW))
    .toEqual([expect.objectContaining({ status: "skipped", reason: "line paused by jason" })]);
  // The Band in force rows read as API-09 serves them: derived, read-only, no readback path.
  expect(set("in-band")["API-09"].items.find((row: Body) => row.param === "band_house_temp_low")).toMatchObject({
    label: "House temp low", group: "band_in_force", control_class: "crop_band", writable: false, readback_via: "none", readback: { value: 62 }, desired: null,
    plan: null, applied_from: "band", state: "no_readback",
  });
  const series = set("series-400d")["API-04"];
  expect(series.bucket_s).toBe(86_400);
  expect(Date.parse(series.to) - Date.parse(series.from)).toBe(400 * 86_400_000);
  for (const key of ["climate.temp_avg", "climate.vpd_avg", "climate.rh_avg"]) expect(series.series[key], key).toMatchObject({ source: "daily_summary", retained_from: new Date(NOW - 365 * 86_400_000).toISOString() });
  // scorecard-30d: the scorecard's first API-04 request, 30 America/Denver day buckets with the 25-hour 2026-11-01,
  // whole-day time in band every day but the day with no row, the day/night split for the last 3 days, and the gallons'
  // reasons; API-IRR-10 covers the same 30 days with its outage day and today partial.
  const scorecard = set("scorecard-30d");
  const daily = scorecard["API-04"];
  expect(daily).toMatchObject({ bucket_s: 86_400, from: "2026-10-04T06:00:00.000Z", to: "2026-11-03T07:00:00.000Z" });
  expect(daily.t).toHaveLength(30);
  expect(daily.t.at(-1) - daily.t.at(-2)).toBe(25 * 3600);
  expect(Object.keys(daily.series)).toHaveLength(8);
  const water = daily.series["daily.water_gal"];
  expect(water).toMatchObject({ source: "scorecard", retained_from: null });
  expect(water.reason.filter(Boolean)).toEqual(["no meter data", "discontinuous", "partial_day"]);
  expect(daily.series["daily.temp_in_band_pct"].avg.filter((value: number | null) => value === null)).toHaveLength(1);
  expect(daily.series["daily.time_in_band_day_pct"].reason.filter(Boolean)).toHaveLength(26);
  expect(scorecard["API-IRR-10"]).toMatchObject({ from: "2026-10-04", to: "2026-11-02" });
  expect(scorecard["API-IRR-10"].periods.filter((period: Body) => period.coverage !== "complete")).toEqual([{ start: "2026-10-24", coverage: "no_data" }, { start: "2026-11-02", coverage: "partial" }]);
  const stuck = set("stuck-delivery");
  expect(stuck["API-02"].writer).toMatchObject({ stuck: true, differing: 4, oldest_since: "2026-11-02T13:10:00.000Z" });
  expect(stuck["API-14"].items).toEqual([expect.objectContaining({ alert_type: "setpoint_delivery_stuck" })]);
  expect(set("iris-unsnapped")["API-09"].items.find((row: Body) => row.param === "mister_engage_delay_s")).toMatchObject({ step: 30, plan: { value: 48.75 } });
});

test("Now's fixtures (#1129): too hot alone, a null RH, band rows with no time, and the occupancy states", () => {
  const set = (name: string | string[]) => fixture(name, { now: NOW });
  const hot = set("too-hot")["API-02"];
  expect(hot).toMatchObject({ indoor: { temp_f: { value: 84.1, observed_at: minutesAgo(1 / 3) } }, band: { temp_f: { low: 62, high: 70, verdict: "above" } }, alerts: { open: 0 } });
  expect(hot.freshness).toMatchObject({ ingestor: { stale: false }, climate: { stale: false } });
  expect(set("rh-missing")["API-02"].indoor.rh_pct).toEqual({ value: null, observed_at: minutesAgo(1 / 3), reason: "No indoor RH" });
  expect(set("band-age-unknown")["API-02"].band).toMatchObject({ temp_f: { low: 62, high: 70 }, observed_at: null });
  expect(set("occupied")["API-IRR-01"].occupancy).toEqual({ occupied: true, seen_at: minutesAgo(4), hold_enabled: true });
  expect(set("occupancy-unknown")["API-IRR-01"].occupancy).toEqual({ occupied: null, seen_at: null, hold_enabled: true });
  expect(set("occupancy-hold-off")["API-IRR-01"].occupancy).toMatchObject({ hold_enabled: false });
});

test("fixture() reads requested-to-confirmed and flow-after-stop after their writes; the fixture API starts before them", () => {
  const row = (set: Record<string, Body>) => ((set["API-09"] as Body).items as Body[]).find((item) => item.param === "mister_engage_kpa")!;
  const confirmed = fixture("requested-to-confirmed", { now: NOW });
  expect(confirmed["API-10"]).toMatchObject({ value: 1.55, snapped_from: 1.53, state: "requested", manual_before: false });
  expect(row(confirmed)).toMatchObject({ state: "confirmed", applied_from: "manual", readback: { value: 1.55 }, desired: { value: 1.55, state: "confirmed", state_reason: null } });
  const refused = fixture("requested-to-confirmed", { now: NOW, confirm: false });
  expect(row(refused)).toMatchObject({ state: "not_confirmed", desired: { state: "not_confirmed", state_reason: { code: "no_match", since: refused["API-10"].created_at } } });
  expect(refused["API-07"].lanes.find((lane: Body) => lane.param === "mister_engage_kpa")).toMatchObject({ applied_from: "manual", desired: { state: "not_confirmed" } });
  expect(row({ "API-09": get(handler("requested-to-confirmed", { confirm: false }), "/v1/settings") })).toMatchObject({ state: "confirmed", desired: null });
  const stopped = fixture("flow-after-stop", { now: NOW });
  expect(stopped["API-IRR-07"]).toMatchObject({ kind: "stop", line_id: null, status: "pending" });
  expect(stopped["API-IRR-01"].lines.find((line: Body) => line.line_id === "L01").state).toMatchObject({ code: "idle" });
  expect(stopped["API-02"]).toMatchObject({ running: [], leak: { active: true, alert_id: 201 } });
  expect(get(handler("flow-after-stop"), "/v1/now")).toMatchObject({ running: [expect.objectContaining({ line_id: "L01" })], leak: { active: false } });
});

test("schedules-paused shows three schedules and a past L02 block skipped because the line was paused", () => {
  const api = handler("schedules-paused");
  const agenda = get(api, "/v1/irrigation/schedules?past_days=7");
  expect(agenda.schedules).toHaveLength(3);
  expect(agenda.counts).toEqual({ schedules: 3, max_schedules: 24 });
  const l02 = (agenda.occurrences as Body[]).filter((row) => row.line_id === "L02" && Date.parse(row.planned_start as string) < NOW);
  expect(l02.length).toBeGreaterThan(0);
  for (const row of l02) expect(row).toMatchObject({ status: "skipped", reason: "line paused by jason" });
  expect((agenda.occurrences as Body[]).some((row) => row.line_id === "L01" && row.status === "planned")).toBe(true);
  expect((get(api, "/v1/irrigation/schedules").occurrences as Body[]).every((row) => Date.parse(row.planned_start as string) >= Date.parse("2026-11-02T07:00:00Z"))).toBe(true);
});

test("flow-after-stop: after a Stop the relays read OFF while the flow continues", () => {
  const api = handler("flow-after-stop");
  expect(get(api, "/v1/now").leak).toMatchObject({ active: false });
  at(api, "POST", "/v1/irrigation/stop", {});
  expect(line(api, "L01").state).toMatchObject({ code: "idle" });
  expect(get(api, "/v1/now").leak).toMatchObject({ active: true, alert_id: 201 });
});

test("acknowledging lowers the one open-alert count by one and logs alert.ack", () => {
  const api = handler("alerts-merged");
  const before = get(api, "/v1/now").alerts.open;
  const acked = at(api, "POST", "/v1/alerts/101/ack", { note: "breaker reset" }, KEY);
  expect(acked.body).toMatchObject({ id: 101, status: "acknowledged", acknowledged_by: "jason", notes: "breaker reset" });
  expect(get(api, "/v1/now").alerts.open).toBe(before - 1);
  expect(get(api, "/v1/activity?action=alert.ack").items[0]).toMatchObject({ target: "101", request_id: KEY });
  expect(at(api, "POST", "/v1/alerts/999/ack", {}).body).toMatchObject({ status: 422, code: "unknown_target" });
});

test("the listener serves HTTP with no-store, the /api prefix and a 16 KiB body cap", async () => {
  const api = handler();
  const now = await request(api).get("/v1/now");
  expect(now.status).toBe(200);
  expect(now.headers["cache-control"]).toBe("no-store");
  expect(now.body.indoor.temp_f.value).toBe(64.8);
  expect((await request(api).get("/api/v1/me")).body).toMatchObject({ role: "operator" });
  expect((await request(api).get("/v1/nope")).status).toBe(404);
  expect((await request(api).delete("/v1/now")).headers.allow).toBe("GET");
  const big = await request(api).put("/v1/settings/mister_engage_kpa").set("Content-Type", "application/json").send(JSON.stringify({ reason: "x".repeat(17_000) }));
  expect(big.status).toBe(413);
  const login = await request(api).get("/auth/login?returnTo=%2Fwater");
  expect(login.status).toBe(302);
  expect(login.headers.location).toBe("/auth/callback?code=fixture&state=%2Fwater");
  expect((await request(api).get("/auth/callback?code=fixture&state=%2Fwater")).headers.location).toBe("/water");
  expect((await request(api).get("/auth/login?returnTo=https%3A%2F%2Fevil.example")).headers.location).toBe("/auth/callback?code=fixture&state=%2F");
});

test("as a server it starts only in approved in-cluster CI", () => {
  expect(() => startFixtureServer({})).toThrow("Fixture API requires approved in-cluster CI");
  expect(() => startFixtureServer({ DASHBOARD_CI_IN_CLUSTER: "0" })).toThrow("Fixture API requires approved in-cluster CI");
});
