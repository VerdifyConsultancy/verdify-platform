// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * Equipment's depth (#1140) against #1122's fixture API in process: the Why panel's words, its age when the controller is
 * not reporting and its Graphs window; a light's Turn on, Turn off and Back to automatic, with who gets them, the one
 * request per tap, the Lutron readback, a 503's words and the resync only after CONFIRMED. The clock is the screens' 10:42
 * America/Denver on 2026-11-02.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { FakeResizeObserver, MockPlot, media, setTokens } from "../charts/fixtures/uplot-mock";
import { automationWords, lightButtons, resyncState, unconfirmedWords } from "../components/equipment/light-controls";
import { WHY_WINDOW_MS, whyGraphUrl } from "../components/equipment/why-panel";
import { text } from "../components/test-render";
import { ToastProvider } from "../components/toast";
import { SessionProvider, type Role } from "../lib/session";
import { UUID, advance, requests, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Equipment from "./equipment";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

/** What a mounted chart needs in jsdom: uPlot's stand-in, ResizeObserver, matchMedia and the tokens. */
function chartStubs() {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ narrow: false, fine: true }));
  setTokens();
}
beforeEach(chartStubs);
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;
/**
 * The route as an operator with writes on sees it, over the fixture API, once its reads and lanes have settled. `wrap`
 * can alter the fixture's answers before the first read.
 */
async function load(options: FixtureOptions = {}, { role = "operator", writesEnabled = true, wrap }: { role?: Role; writesEnabled?: boolean; wrap?: (fetch: Fetch) => Fetch } = {}) {
  const api = startFixtureApi(options);
  if (wrap) vi.stubGlobal("fetch", vi.fn(wrap(globalThis.fetch as Fetch)));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/equipment"]}><SessionProvider role={role} writesEnabled={writesEnabled}><ToastProvider><Equipment /></ToastProvider></SessionProvider></MemoryRouter></QueryClientProvider>);
  await advance(1);
  await act(async () => { await vi.dynamicImportSettled(); });
  await advance(1);
  return api;
}
/** A read's answer reaches the page on the tick after it (TanStack batches its notifications on a timer). */
const tick = async (ms: number) => { await advance(ms); await advance(1); };

const reason = (key: string) => document.querySelector<HTMLElement>(`.why-row[data-reason="${key}"]`)!;
const words = (key: string) => text(reason(key).querySelector(".why-words"));
const link = (key: string) => reason(key).querySelector("a")?.getAttribute("href") ?? null;

const row = (key: string) => document.querySelector<HTMLElement>(`.equipment-row[data-equipment="${key}"]`)!;
const state = (key: string) => text(row(key).querySelector(".equipment-state"));
const button = (key: string, name: RegExp) => within(row(key)).queryByRole("button", { name });
const press = (key: string, name: RegExp) => fireEvent.click(button(key, name)!);
const auto = (key: string) => text(row(key).querySelector("[data-auto]")).trim();
const note = (key: string) => row(key).querySelector("[data-light-note]")?.textContent ?? null;
const refusal = (key: string) => text(row(key).querySelector(".light-controls .field-error"));
const lightWrites = (api: Awaited<ReturnType<typeof load>>) => requests(api, "API-20").map((sent) => [sent.path, sent.body]);
/** The parts of a 503 the client reads, resolving in microtasks as the fixture API's answers do. */
const refusal503 = (problem: object) => ({ status: 503, ok: false, type: "basic", headers: new Headers(), json: async () => problem, text: async () => "" }) as unknown as Response;
/** API-09 reads the main light's automatic switch off (0), as after someone switched its automation off: Back to automatic shows. */
const automationOff = (base: Fetch): Fetch => async (input, init) => {
  const response = await base(input, init);
  if (new URL(input, "http://dashboard.invalid").pathname !== "/api/v1/settings") return response;
  const body = await response.json() as { items: { param: string; readback: object | null }[] };
  const items = body.items.map((item) => (item.param === "sw_gl_main_auto_mode" && item.readback ? { ...item, readback: { ...item.readback, value: 0 } } : item));
  return { ...response, headers: response.headers, json: async () => ({ ...body, items }) } as unknown as Response;
};
/** Every POST answers 503 with `problem`, and never reaches the fixture API; the reads still do. */
function refuseWrites(problem: object) {
  const real = globalThis.fetch;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => (init?.method === "POST" ? refusal503(problem) : real(input, init))));
}
/** Polls every 5 s until `done`, as many reads as it takes the fixture's readback to settle (its third API-09 read). */
async function poll(done: () => boolean, reads = 6) {
  for (let read = 0; read < reads && !done(); read += 1) await tick(5000);
  expect(done()).toBe(true);
}

// ---------------------------------------------------------------------------------------------------- the Why panel
test("each reason reads in words with when it began and its Graphs window; the panel sits between the board and the lanes", async () => {
  await load({ fixture: "why-reasons" });
  expect([...document.querySelectorAll(".why-row")].map((node) => text(node.querySelector(".why-label")))).toEqual(["Climate mode", "Fog", "Misters (center, south, west)"]);
  // Yesterday's reads with its date, and today's with its time alone, on the America/Denver clock.
  expect(words("mode_reason")).toBe("Venting: too warm · since Nov 1, 13:40");
  expect(words("fog_block_reason")).toBe("Humidity at the fog ceiling · since 09:12");
  expect(words("moisture_block_reason")).toBe("VPD in band: no misting needed · since 07:12");
  expect(reason("fog_block_reason").querySelector("time")?.getAttribute("dateTime")).toBe("2026-11-02T16:12:00.000Z");
  expect(link("mode_reason")).toBe("/graphs?from=2026-11-01T18:40:00Z&to=2026-11-01T22:40:00Z");
  expect(link("fog_block_reason")).toBe("/graphs?from=2026-11-02T14:12:00Z&to=2026-11-02T18:12:00Z");
  expect(link("moisture_block_reason")).toBe("/graphs?from=2026-11-02T12:12:00Z&to=2026-11-02T16:12:00Z");
  const board = document.querySelector(".equipment-board")!, why = reason("mode_reason");
  const lanes = screen.getByRole("heading", { level: 2, name: "Last 24 h" });
  expect(board.compareDocumentPosition(why) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(why.compareDocumentPosition(lanes) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("the link's window is 2 h either side of since, and a time that cannot be read links nowhere", () => {
  expect(WHY_WINDOW_MS).toBe(7_200_000);
  expect(whyGraphUrl("2026-11-02T16:12:00.000Z")).toBe("/graphs?from=2026-11-02T14:12:00Z&to=2026-11-02T18:12:00Z");
  expect(whyGraphUrl("soon")).toBeNull();
});

test("a code the table lacks renders raw in mono, with its since and link; the firmware's own unknown reads as a value", async () => {
  await load({ fixture: "why-unknown-code" });
  expect(reason("mode_reason").querySelector("code")?.textContent).toBe("brand_new_code");
  expect(words("mode_reason")).toBe("brand_new_code · since 09:55");
  expect(link("mode_reason")).toBe("/graphs?from=2026-11-02T14:55:00Z&to=2026-11-02T18:55:00Z");
  expect(reason("fog_block_reason").querySelector("code")).toBeNull();
});

test("with the controller not reporting every reason reads unknown with its age, never its last words, and links nowhere", async () => {
  await load({ fixture: ["why-reasons", "controller-not-reporting"] });
  for (const key of ["mode_reason", "fog_block_reason", "moisture_block_reason"]) {
    expect(words(key), key).toBe("unknown · last report 1 min ago");
    expect(reason(key).getAttribute("data-reason"), key).toBe(key);
    expect(link(key), key).toBeNull();
  }
  expect(text(document.querySelector(".why-rows"))).not.toMatch(/Venting|ceiling|misting/);
});

test("the panel fails with the board on its one API-13 read, every 15 s, and Retry reads it again", async () => {
  const api = await load({ fail: { "API-13": 503 } });
  const panel = screen.getByRole("region", { name: "Why" });
  expect(text(panel.querySelector(".fault"))).toBe("Couldn't load the reasons.");
  expect(requests(api, "API-13")).toHaveLength(1);
  api.respond({ method: "POST", url: "/__control", body: { fixture: "why-reasons" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Retry" }));
  await tick(1);
  expect(words("fog_block_reason")).toBe("Humidity at the fog ceiling · since 09:12");
  const reads = sentAt("API-13").length;
  await tick(15_000);
  expect(sentAt("API-13")).toHaveLength(reads + 1);
});

// ---------------------------------------------------------------------------------------------------- who gets the controls
test("a viewer gets the board's words and no light control; with changes switched off they are disabled", async () => {
  await load({ fixture: "in-band" }, { role: "viewer", writesEnabled: false });
  for (const key of ["grow_light_main", "grow_light_grow"]) {
    expect(within(row(key)).queryAllByRole("button"), key).toEqual([]);
    // The one link is the automatic switch's setting, which a viewer opens read-only.
    const light = key === "grow_light_main" ? "Main" : "Grow";
    expect(within(row(key)).getAllByRole("link").map((link) => [text(link), link.getAttribute("href")]), key).toEqual([
      [`Automation setting of ${light}›`, `/equipment?edit=sw_gl_${key === "grow_light_main" ? "main" : "grow"}_auto_mode`],
    ]);
    expect(auto(key), key).toBe("Automatic");
  }
  cleanup();
  stopFixtureApi();
  chartStubs();
  // Main is off in automatic; Grow differs from its controller, so it is overridden: each its own buttons, all disabled.
  await load({ fixture: ["writes-off", "grow-lights-differ"] }, { writesEnabled: false });
  for (const [key, names] of [["grow_light_main", ["Turn on 15 min Main"]], ["grow_light_grow", ["Back to automatic Grow", "Turn on 15 min Grow"]]] as const) {
    const buttons = within(row(key)).getAllByRole("button");
    expect(buttons.map((node) => text(node)), key).toEqual(names);
    for (const node of buttons) expect((node as HTMLButtonElement).disabled, key).toBe(true);
  }
});

test("each light links to its Activity log beside its automatic switch's setting, and each button is a wide 44 px write control", async () => {
  await load({ fixture: "grow-lights-differ" });
  expect(within(row("grow_light_main")).getByRole("link", { name: "Activity of Main" }).getAttribute("href")).toBe("/activity/log?target=grow_light_main");
  expect(within(row("grow_light_grow")).getByRole("link", { name: "Activity of Grow" }).getAttribute("href")).toBe("/activity/log?target=grow_light_grow");
  expect(within(row("grow_light_main")).getByRole("link", { name: "Automation setting of Main" }).getAttribute("href")).toBe("/equipment?edit=sw_gl_main_auto_mode");
  // The links sit together right of the automatic line, never among the buttons.
  expect([...row("grow_light_main").querySelectorAll(".light-auto-row .light-links a")].map((node) => text(node))).toEqual(["Automation setting of Main›", "Activity of Main›"]);
  expect(document.querySelectorAll(".light-actions a")).toHaveLength(0);
  const buttons = [...document.querySelectorAll<HTMLElement>(".light-actions button")];
  expect(buttons.map((node) => text(node))).toEqual(["Turn on 15 min Main", "Back to automatic Grow", "Turn on 15 min Grow"]);
  for (const node of buttons) {
    expect(node.className).toContain("btn-wide");
    expect(node.getAttribute("data-write-control")).toBe("true");
  }
});

test("a light offers the buttons its state calls for: the switch to its other state in automatic, Back to automatic first when overridden (#1308 visual QA 26)", async () => {
  const names = (shown: ReturnType<typeof lightButtons>) => [...(shown.automatic ? ["automatic"] : []), ...shown.turns];
  // In automatic and matching the controller: the one switch to the other state.
  expect(names(lightButtons({ intent: "off", state: "off" }, 1, false))).toEqual(["on"]);
  expect(names(lightButtons({ intent: "on", state: "on" }, 1, false))).toEqual(["off"]);
  // Overridden: Lutron differs from the controller, or automation is off: Back to automatic first, then the switch.
  expect(names(lightButtons({ intent: "off", state: "on" }, 1, false))).toEqual(["automatic", "off"]);
  expect(names(lightButtons({ intent: "off", state: "off" }, 0, false))).toEqual(["automatic", "on"]);
  // Not known: automation not reported, the intent or Lutron unknown, never a guess that hides a control.
  expect(names(lightButtons({ intent: "off", state: "off" }, null, false))).toEqual(["automatic", "on"]);
  expect(names(lightButtons({ intent: null, state: "off" }, 1, false))).toEqual(["automatic", "on"]);
  expect(names(lightButtons({ intent: "on", state: "unknown" }, 1, false))).toEqual(["automatic", "on", "off"]);
  // A command on its way keeps both switches, so the other may override it.
  expect(names(lightButtons({ intent: "off", state: "off" }, 1, true))).toEqual(["on", "off"]);

  const api = await load({ fixture: "lights-confirmed" });
  const shown = () => within(row("grow_light_main")).queryAllByRole("button").map((node) => text(node));
  expect(shown()).toEqual(["Turn on 15 min Main"]);
  const timed = () => row("grow_light_main").querySelector("[data-light-timed]")?.textContent ?? null;
  expect(timed()).toBeNull();
  press("grow_light_main", /^Turn on/);
  await advance(0);
  // Reading back: both switches, the pressed one waiting.
  expect(shown()).toEqual(["Turn on 15 min Main", "Turn off 15 min Main"]);
  await tick(5000);
  // Lutron on, the controller off: overridden by hand.
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron On Differ");
  expect(shown()).toEqual(["Back to automatic Main", "Turn off 15 min Main"]);
  // A manual switch is timed like the Fans burst (#1335): API-20's default 15 min, so the page sends no minutes.
  expect(timed()).toBe("A Turn on or Turn off lasts 15 min, then the light goes back to automatic");
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/main/on", null]]);
});

// ---------------------------------------------------------------------------------------------------- Turn on, Turn off
test("two fast taps on Turn on send one API-20 with one key, reading Sending…; the board's state comes from the readback", async () => {
  const api = await load({ fixture: "lights-confirmed" });
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
  const pressed = button("grow_light_main", /^Turn on/)!;
  fireEvent.click(pressed);
  expect(text(pressed)).toBe("Sending…");
  fireEvent.click(pressed);
  await advance(0);
  const sent = writes(api);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ method: "POST", path: "/api/v1/equipment/lights/main/on", operationId: "API-20", body: null });
  expect(sent[0].idempotency_key).toMatch(UUID);
  // The 200 alone does not move the board: only API-13 does, 5 s later.
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
  expect(note("grow_light_main")).toBe("Waiting for Lutron");
  await tick(5000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron On Differ");
  expect(note("grow_light_main")).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("while a command reads back its own button has nothing to add, and the other may still override it", async () => {
  const api = await load({ fixture: "lights-confirmed", fail: {} });
  press("grow_light_main", /^Turn on/);
  await advance(0);
  expect((button("grow_light_main", /^Turn on/) as HTMLButtonElement).disabled).toBe(true);
  expect((button("grow_light_main", /^Turn off/) as HTMLButtonElement).disabled).toBe(false);
  press("grow_light_main", /^Turn off/);
  await advance(0);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/main/on", null], ["/api/v1/equipment/lights/main/off", null]]);
  await tick(5000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
});

for (const [fixture, says] of [["lights-ha-fail", "Lutron did not switch: Home Assistant did not answer"], ["lights-not-confirmed", "Lutron did not reach the state"]] as const) {
  test(`${fixture}: the result in words and the readback kept; it reads API-13 for 30 s, then says still OFF and drops to 15 s`, async () => {
    const api = await load({ fixture });
    press("grow_light_main", /^Turn on/);
    await advance(0);
    expect(refusal("grow_light_main")).toBe(says);
    expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
    expect(writes(api)).toHaveLength(1);
    const before = sentAt("API-13").length;
    await tick(25_000);
    expect(note("grow_light_main")).toBeNull();
    await tick(5000);
    expect(sentAt("API-13").length - before).toBeGreaterThanOrEqual(6);
    expect(note("grow_light_main")).toBe("Lutron hasn't confirmed: still OFF");
    expect(refusal("grow_light_main")).toBe(says);
    expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
    const after = sentAt("API-13").length;
    await tick(30_000);
    expect(sentAt("API-13").length - after).toBeLessThanOrEqual(2);
    expect(writes(api)).toHaveLength(1);
  });
}

test("a light that switches late, after a 503, takes back the words: the readback answers", async () => {
  const api = await load({ fixture: "lights-ha-fail" });
  press("grow_light_main", /^Turn on/);
  await advance(0);
  expect(refusal("grow_light_main")).toContain("Home Assistant");
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lights-confirmed" } });
  api.respond({ method: "POST", url: "/v1/equipment/lights/main/on" });
  await tick(5000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron On Differ");
  expect(row("grow_light_main").querySelector(".light-controls .field-error")).toBeNull();
  expect(note("grow_light_main")).toBeNull();
});

test("no match after 30 s, and a switch on a later poll clears the note", async () => {
  const api = await load({ fixture: "lights-not-confirmed" });
  press("grow_light_main", /^Turn on/);
  await advance(0);
  await tick(30_000);
  expect(note("grow_light_main")).toBe("Lutron hasn't confirmed: still OFF");
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lights-confirmed" } });
  api.respond({ method: "POST", url: "/v1/equipment/lights/main/on" });
  await tick(15_000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron On Differ");
  expect(note("grow_light_main")).toBeNull();
});

test("no answer reaching the page reads the Lutron state back too, and changes switched off send nothing to read back", async () => {
  const api = await load({ fixture: "lights-confirmed" });
  refuseWrites({ title: "Unavailable", status: 503, code: "unavailable", retryable: true, detail: "The operator API did not answer. Try again." });
  press("grow_light_main", /^Turn on/);
  await advance(0);
  expect(refusal("grow_light_main")).toBe("The operator API did not answer. Try again.");
  const reads = sentAt("API-13").length;
  await tick(30_000);
  expect(sentAt("API-13").length).toBeGreaterThanOrEqual(reads + 6);
  expect(note("grow_light_main")).toBe("Lutron hasn't confirmed: still OFF");
  expect(writes(api)).toEqual([]);
  cleanup();
  stopFixtureApi();
  chartStubs();
  await load({ fixture: "lights-confirmed" });
  refuseWrites({ title: "Off", status: 503, code: "writes_disabled", retryable: false });
  const before = sentAt("API-13").length;
  press("grow_light_main", /^Turn on/);
  await advance(0);
  expect(refusal("grow_light_main")).toBe("Changes are switched off");
  expect(note("grow_light_main")).toBeNull();
  await tick(10_000);
  expect(sentAt("API-13").length).toBe(before);
});

test("the words of a 503 follow the readback's state, and an unknown one never says OFF", () => {
  expect(unconfirmedWords("off")).toBe("Lutron hasn't confirmed: still OFF");
  expect(unconfirmedWords("on")).toBe("Lutron hasn't confirmed: still ON");
  expect(unconfirmedWords("unknown")).toBe("Lutron hasn't confirmed: its state is unknown");
  expect(unconfirmedWords(undefined)).toBe("Lutron hasn't confirmed: its state is unknown");
});

// ---------------------------------------------------------------------------------------------------- Back to automatic
test("the resync sends the intent's state only when the intent and Lutron are both known and differ", () => {
  expect(resyncState({ intent: "off", state: "on" })).toBe("off");
  expect(resyncState({ intent: "on", state: "off" })).toBe("on");
  for (const same of [{ intent: "on", state: "on" }, { intent: "off", state: "off" }]) expect(resyncState(same)).toBeNull();
  for (const unknown of [{ intent: null, state: "on" }, { intent: undefined, state: "off" }, { intent: "unknown", state: "on" }, { intent: "on", state: "unknown" }]) expect(resyncState(unknown as never)).toBeNull();
  expect(resyncState(null)).toBeNull();
});

test("the row's automatic line reads the device value, and a value not reported is never off", () => {
  expect(automationWords({ readback: { value: 1, observed_at: null, reason: null } })).toBe("Automatic");
  expect(automationWords({ readback: { value: 0, observed_at: null, reason: null } })).toBe("Automation off");
  expect(automationWords({ readback: { value: null, observed_at: null, reason: "not reported yet" } })).toBe("Automation — not reported yet");
  expect(automationWords(undefined)).toBe("Automation —");
});

test("with API-09 failing the automatic line reads —, never its last value, and the controls still work", async () => {
  const api = await load({ fixture: "lights-confirmed", fail: { "API-09": 503 } });
  expect(auto("grow_light_main")).toBe("Automation —");
  press("grow_light_main", /^Back to automatic/);
  await advance(0);
  expect(requests(api, "API-10").map((sent) => sent.path)).toEqual(["/api/v1/settings/sw_gl_main_auto_mode"]);
  // No row to confirm it, so no resync until API-09 answers again.
  await tick(5000);
  expect(lightWrites(api)).toEqual([]);
});

test("Back to automatic: one API-10 write with manual off (#1335), REQUESTED then CONFIRMED, and only then one API-20 to the intent's state", async () => {
  const api = await load({ fixture: ["lights-confirmed", "requested-to-confirmed"] });
  // Turned on by hand first: intent OFF with Lutron ON.
  press("grow_light_main", /^Turn on/);
  await advance(0);
  await tick(5000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron On Differ");
  expect(auto("grow_light_main")).toBe("Automatic");

  const back = button("grow_light_main", /^Back to automatic/)!;
  fireEvent.click(back);
  expect(text(back)).toBe("Sending…");
  fireEvent.click(back);
  await advance(0);
  const puts = requests(api, "API-10");
  expect(puts).toHaveLength(1);
  expect(puts[0]).toMatchObject({ path: "/api/v1/settings/sw_gl_main_auto_mode", body: { value: 1, manual: false, reason: "Back to automatic" } });
  expect(puts[0].idempotency_key).toMatch(UUID);
  await tick(1);
  expect(auto("grow_light_main")).toBe("Requested");
  expect((button("grow_light_main", /^Back to automatic/) as HTMLButtonElement).disabled).toBe(true);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/main/on", null]]);

  // While it is REQUESTED API-09 is read every 5 s, and nothing more is sent.
  const reads = sentAt("API-09").length;
  await tick(5000);
  expect(sentAt("API-09")).toHaveLength(reads + 1);
  expect(lightWrites(api)).toHaveLength(1);
  await poll(() => auto("grow_light_main").startsWith("Confirmed"));
  await advance(1);
  expect(auto("grow_light_main")).toBe("Confirmed Automatic");
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/main/on", null], ["/api/v1/equipment/lights/main/off", { reason: "Back to automatic" }]]);
  // Lutron follows on the readback, and the row sends no more.
  await tick(5000);
  expect(state("grow_light_main")).toBe("Off (controller) · Lutron Off");
  await tick(30_000);
  expect(lightWrites(api)).toHaveLength(2);
  expect(requests(api, "API-10")).toHaveLength(1);
});

test("Back to automatic on grow-lights-differ sends one on request once CONFIRMED", async () => {
  const api = await load({ fixture: ["grow-lights-differ", "requested-to-confirmed"] });
  expect(state("grow_light_grow")).toBe("On (controller) · Lutron Off Differ");
  press("grow_light_grow", /^Back to automatic/);
  await advance(0);
  expect(requests(api, "API-10")[0].path).toBe("/api/v1/settings/sw_gl_grow_auto_mode");
  await poll(() => auto("grow_light_grow").startsWith("Confirmed"));
  await advance(1);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/grow/on", { reason: "Back to automatic" }]]);
});

for (const fixture of ["lights-intent-unknown", "ha-down"]) {
  test(`Back to automatic with ${fixture} sends no API-20: the two are not both known`, async () => {
    const api = await load({ fixture: [fixture, "requested-to-confirmed"] });
    press("grow_light_main", /^Back to automatic/);
    await advance(0);
    await poll(() => auto("grow_light_main").startsWith("Confirmed"));
    await tick(5000);
    expect(lightWrites(api)).toEqual([]);
  });
}

test("Back to automatic with the two agreeing sends none, and a write that never confirms sends none either", async () => {
  // Automation off with Lutron and the controller agreeing: Back to automatic shows, and once CONFIRMED there is nothing to match.
  const api = await load({ fixture: ["lights-confirmed", "requested-to-confirmed"] }, { wrap: automationOff });
  press("grow_light_main", /^Back to automatic/);
  await advance(0);
  await poll(() => auto("grow_light_main").startsWith("Confirmed"));
  await tick(5000);
  expect(lightWrites(api)).toEqual([]);
  cleanup();
  stopFixtureApi();
  chartStubs();
  const refused = await load({ fixture: ["grow-lights-differ", "requested-to-confirmed"], confirm: false });
  press("grow_light_grow", /^Back to automatic/);
  await advance(0);
  await poll(() => auto("grow_light_grow").startsWith("Not confirmed"));
  await tick(30_000);
  expect(lightWrites(refused)).toEqual([]);
  expect((button("grow_light_grow", /^Back to automatic/) as HTMLButtonElement).disabled).toBe(false);
});

test("a Turn on while the write waits supersedes the resync: nothing but that Turn on is sent", async () => {
  const api = await load({ fixture: ["grow-lights-differ", "requested-to-confirmed"] });
  press("grow_light_grow", /^Back to automatic/);
  await advance(0);
  await tick(1);
  press("grow_light_grow", /^Turn on/);
  await advance(0);
  await poll(() => auto("grow_light_grow").startsWith("Confirmed") || auto("grow_light_grow") === "Automatic");
  await tick(30_000);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/grow/on", null]]);
});

test("Back to automatic takes the place of the last command's words, and a Turn on of a refused write's", async () => {
  // Automation off, so Back to automatic shows beside Turn on.
  const api = await load({ fixture: ["lights-not-confirmed", "requested-to-confirmed"] }, { wrap: automationOff });
  const says = () => row("grow_light_main").querySelector(".light-controls .field-error");
  press("grow_light_main", /^Turn on/);
  await advance(0);
  await tick(30_000);
  expect(text(says())).toBe("Lutron did not reach the state");
  expect(note("grow_light_main")).toBe("Lutron hasn't confirmed: still OFF");
  press("grow_light_main", /^Back to automatic/);
  await advance(0);
  expect(says()).toBeNull();
  expect(note("grow_light_main")).toBeNull();
  // The intent and Lutron agree, so no command follows, and the row says nothing of the old one.
  await poll(() => auto("grow_light_main").startsWith("Confirmed"));
  await tick(5000);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/main/on", null]]);
  expect(says()).toBeNull();
  expect(note("grow_light_main")).toBeNull();
  cleanup();
  stopFixtureApi();
  chartStubs();
  await load({ fixture: "lights-confirmed", fail: { "API-10": 503 } }, { wrap: automationOff });
  press("grow_light_main", /^Back to automatic/);
  await advance(0);
  expect(says()).not.toBeNull();
  press("grow_light_main", /^Turn on/);
  await advance(0);
  expect(says()).toBeNull();
  expect(note("grow_light_main")).toBe("Waiting for Lutron");
});

/** API-13 answers 503 while `failing` says so; every other read goes on to the fixture API. */
function failingBoard(failing: () => boolean) {
  const real = globalThis.fetch;
  const problem = { title: "Unavailable", status: 503, code: "unavailable", retryable: true };
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => (failing() && new URL(input, "http://dashboard.invalid").pathname === "/api/v1/equipment" ? refusal503(problem) : real(input, init))));
}

test("a failed API-13 poll replaces the groups, and the write and its resync outlive it", async () => {
  const api = await load({ fixture: ["grow-lights-differ", "requested-to-confirmed"] });
  let failing = false;
  failingBoard(() => failing);
  // The write goes just before the board's next poll, so it reads REQUESTED when that poll fails.
  await tick(12_000);
  press("grow_light_grow", /^Back to automatic/);
  await advance(0);
  failing = true;
  await tick(3000);
  expect(document.querySelector(".equipment-rows")).toBeNull();
  expect(text(document.querySelector(".equipment-error .fault"))).toBe("Couldn't load equipment.");
  expect(requests(api, "API-10")).toHaveLength(1);
  failing = false;
  // API-09's third read after the write confirms it, and API-13 answers the resync's read.
  await poll(() => lightWrites(api).length > 0);
  expect(lightWrites(api)).toEqual([["/api/v1/equipment/lights/grow/on", { reason: "Back to automatic" }]]);
  await tick(5000);
  expect(document.querySelector(".equipment-rows")).not.toBeNull();
  expect(auto("grow_light_grow")).toBe("Confirmed Automatic");
});

test("the read after CONFIRMED failing sends nothing and says the light was not matched", async () => {
  const api = await load({ fixture: ["grow-lights-differ", "requested-to-confirmed"] });
  let failing = false;
  failingBoard(() => failing);
  press("grow_light_grow", /^Back to automatic/);
  await advance(0);
  await tick(5000);
  // The next API-09 read confirms it, so the read that follows fails.
  failing = true;
  await tick(5000);
  expect(lightWrites(api)).toEqual([]);
  failing = false;
  await tick(60_000);
  expect(note("grow_light_grow")).toBe("Couldn't read the light, so Lutron was not matched to the controller");
  expect(auto("grow_light_grow")).toBe("Confirmed Automatic");
  expect(lightWrites(api)).toEqual([]);
  // The next press clears the note.
  press("grow_light_grow", /^Turn on/);
  await advance(0);
  expect(note("grow_light_grow")).toBe("Waiting for Lutron");
});

test("a light row says why the controller wants it so (API-13 auto_reason, #1335), in words, for every role", async () => {
  await load({ fixture: "viewer-session" });
  for (const key of ["grow_light_main", "grow_light_grow"]) {
    const note = row(key).querySelector("[data-light-reason]");
    expect(note?.getAttribute("data-light-reason")).toBe("lux_sufficient");
    expect(text(note)).toBe("Controller: Off: enough daylight");
  }
});
