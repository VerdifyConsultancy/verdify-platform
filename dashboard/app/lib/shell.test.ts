// SPDX-License-Identifier: MIT
// shellStatus on #1122's named fixtures (screens §1.2): each banner alone, the order when several are true, the warn
// banners, the one open-alert count and the footer and sign-in helpers. Times are America/Denver with now at 10:42.
import { expect, test } from "vitest";
import { FIXTURE_NAMES, fixture } from "../../e2e/fixtures/index.mjs";
import { parseAlertPage, parseMe, parseNow, type Me, type Now } from "./operator-api";
import { API_DOWN_TEXT, WRITES_OFF_TEXT, buildLine, revisionLabel, shellStatus, signInHref, type ShellBanner } from "./shell";

const NOW = Date.parse("2026-11-02T17:42:00Z");
type Answers = { me: Me | null; now: Now | null; set: Record<string, unknown> };
/** What API-01 and API-02 answer under the fixtures (null where they fail, as under api-down). */
function answers(names: string | string[]): Answers {
  const set = fixture(names, { now: NOW });
  return { me: parseMe(set["API-01"]), now: parseNow(set["API-02"]), set };
}
function status(names: string | string[], failures = 0) {
  const { me, now } = answers(names);
  return shellStatus(me, now, failures);
}
const shown = (banner: ShellBanner | null) => banner && { kind: banner.kind, tone: banner.tone, text: banner.text, to: banner.link?.to ?? null };

test("in band: no banner, the count is 0 and the controller reports", () => {
  expect(status("in-band")).toEqual({ fault: null, warns: [], apiDown: false, openAlerts: 0, ingestorRunning: true, controllerReporting: true });
});

test("API-02 failing twice in a row reads Dashboard API unavailable, and once is not enough", () => {
  const { me, now: down503 } = answers("api-down");
  // Under api-down every /v1 read fails: neither API-01 nor API-02 answers.
  expect([me, down503]).toEqual([null, null]);
  expect(shellStatus(me, null, 1)).toMatchObject({ fault: null, apiDown: false });
  const down = shellStatus(me, null, 2);
  expect(shown(down.fault)).toEqual({ kind: "api_down", tone: "fault", text: API_DOWN_TEXT, to: null });
  expect(down).toMatchObject({ apiDown: true, openAlerts: null, ingestorRunning: null, controllerReporting: null });
  // The last answer is kept, but while the API is down nothing says the controller reports, and the count is unknown:
  // the last one (here 2) would read as current, and a stale 0 as an all clear.
  const { now } = answers("alerts-merged");
  expect(shellStatus(me, now, 1).openAlerts).toBe(2);
  expect(shellStatus(me, now, 3)).toMatchObject({ apiDown: true, ingestorRunning: null, controllerReporting: null, openAlerts: null });
  expect(shellStatus(me, answers("in-band").now, 2).openAlerts).toBeNull();
});

test("a heartbeat 4 min old reads Ingestor not running, whatever the climate age, and links to Now's data tile", () => {
  const result = status("ingestor-not-running");
  expect(shown(result.fault)).toEqual({
    kind: "ingestor", tone: "fault", text: "Ingestor not running since 10:38. The controller keeps running on its own.", to: "/#data",
  });
  expect(result).toMatchObject({ ingestorRunning: false, controllerReporting: false });
});

test("a fresh heartbeat with climate 6 min old reads Controller not reporting, never Controller offline", () => {
  const result = status("controller-not-reporting");
  expect(shown(result.fault)).toEqual({
    kind: "controller", tone: "fault", to: "/equipment",
    text: "Controller not reporting since 10:36. If it has power, it keeps running VPD misting, its caps and (with the irrigation firmware) its schedules.",
  });
  expect(result).toMatchObject({ ingestorRunning: true, controllerReporting: false });
  for (const name of FIXTURE_NAMES) {
    for (const failures of [0, 2]) {
      const { fault, warns } = status(name, failures);
      for (const banner of [fault, ...warns]) expect(banner?.text ?? "", name).not.toMatch(/controller offline/i);
    }
  }
});

test("water flowing with every valve closed names the time and volume, with no Stop, and links to its alert", () => {
  const result = status("flow-valves-closed");
  expect(shown(result.fault)).toEqual({
    kind: "flow", tone: "fault", to: "/activity?alert=201",
    text: "Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min: a leak, or the hose, sink or hydro in use. If no one is using water, close the spigot.",
  });
  expect(result.fault?.text).not.toMatch(/\bStop\b/);
});

test("once flow stops the banner reads Flow stopped at HH:MM until the alert is resolved, never an all clear", () => {
  const result = status("flow-stopped");
  expect(shown(result.fault)).toEqual({ kind: "flow", tone: "fault", text: "Flow stopped at 10:40", to: "/activity?alert=201" });
  // Resolved: the alert id clears and the banner goes.
  const { me, now } = answers("flow-stopped");
  const resolved = { ...now!, leak: { ...now!.leak, alert_id: null } };
  expect(shellStatus(me, resolved, 0).fault).toBeNull();
});

test("the warn banners: settings not reaching the controller links to Requested settings; changes switched off", () => {
  expect(status("stuck-delivery").warns.map(shown)).toEqual([
    { kind: "stuck", tone: "warn", text: "Settings not reaching the controller since 06:10 (4 differ)", to: "/settings?filter=requested" },
  ]);
  expect(status("stuck-delivery").fault).toBeNull();
  expect(status("writes-off").warns.map(shown)).toEqual([{ kind: "writes_off", tone: "warn", text: WRITES_OFF_TEXT, to: null }]);
  expect(WRITES_OFF_TEXT).toBe("Changes are switched off; reading still works");
  // Both at once, in that order; without API-01 nothing says changes are off.
  expect(status(["stuck-delivery", "writes-off"]).warns.map((banner) => banner.kind)).toEqual(["stuck", "writes_off"]);
  expect(shellStatus(null, answers("writes-off").now, 0).warns).toEqual([]);
});

test("one fault banner at a time, worst first: API down, ingestor, controller, flow; the warns still show", () => {
  const every = ["ingestor-not-running", "flow-valves-closed", "stuck-delivery", "writes-off"];
  const first = (names: string[], failures = 0) => status(names, failures).fault?.kind;
  expect(first(every, 2)).toBe("api_down");
  expect(first(every)).toBe("ingestor");
  expect(first(["controller-not-reporting", "flow-valves-closed"])).toBe("controller");
  expect(first(["flow-valves-closed", "stuck-delivery"])).toBe("flow");
  expect(first(["stuck-delivery"])).toBeUndefined();
  const result = status(every, 2);
  expect([result.fault?.kind, ...result.warns.map((banner) => banner.kind)]).toEqual(["api_down", "stuck", "writes_off"]);
  // The ingestor banner wins over a stale climate row: it names the side that is down.
  expect(first(["controller-not-reporting", "ingestor-not-running"])).toBe("ingestor");
});

test("the nav count is API-02 alerts.open: the open, unacknowledged rows API-14 lists, repeats counted once", () => {
  const { set } = answers("alerts-merged");
  const page = parseAlertPage(set["API-14"]);
  const open = page!.items.filter((alert) => alert.status === "open");
  expect(open.length).toBe(2);
  expect(status("alerts-merged").openAlerts).toBe(open.length);
  expect(status("in-band").openAlerts).toBe(0);
  expect(shellStatus(null, null, 0).openAlerts).toBeNull();
});

test("the footer's build line, and the sign-in link that returns to the same path and query", () => {
  expect(revisionLabel("0123456789ABCDEF0123456789abcdef01234567")).toBe("0123456789ab");
  expect(revisionLabel("unknown")).toBe("unknown");
  expect(revisionLabel(null)).toBe("—");
  expect(buildLine("0123456789abcdef0123456789abcdef01234567", null)).toBe("Verdify dashboard · build 0123456789ab · API —");
  expect(buildLine("unknown", "fedcba9876543210fedcba9876543210fedcba98")).toBe("Verdify dashboard · build unknown · API fedcba987654");
  expect(signInHref("/activity?target=L01")).toBe("/auth/login?returnTo=%2Factivity%3Ftarget%3DL01");
  expect(signInHref("")).toBe("/auth/login?returnTo=%2F");
  // A page longer than the 2,048 characters the BFF keeps, or one whose encoded form would pass the 4,096-character
  // target server.mjs serves (414), signs in back to /, as the BFF's own sign-in redirect does: the link always works.
  expect(signInHref(`/graphs?${"m=vpd&".repeat(400)}`)).toBe("/auth/login?returnTo=%2F");
  expect(signInHref(`/graphs?q=${"/".repeat(1400)}`)).toBe("/auth/login?returnTo=%2F");
  const fits = `/graphs?q=${"a".repeat(2000)}`;
  expect(signInHref(fits)).toBe(`/auth/login?returnTo=${encodeURIComponent(fits)}`);
  expect(signInHref(fits).length).toBeLessThanOrEqual(4096);
});
