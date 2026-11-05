// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The setting drawer's change history and Revert (#1139, redesign §5.10) against #1122's fixture API in process, with
// uPlot mocked. The clock is 10:42 America/Denver on 2026-11-02 (MST).
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import type { Setting } from "../lib/operator-api";
import { NOW, OPERATOR, UUID, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, writes, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { NO_READBACK_TEXT, PLAN_AHEAD_S, SettingHistoryPanel, type SettingHistoryProps } from "./setting-history";
import { text } from "./test-render";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const S = NOW / 1000;
const DAY_S = 86_400;
const iso = (epochSeconds: number) => new Date(epochSeconds * 1000).toISOString();
const ENGAGE = "mister_engage_kpa";

function Here() {
  const { pathname, search } = useLocation();
  return <output data-testid="here">{`${pathname}${search}`}</output>;
}
const here = () => screen.getByTestId("here").textContent;
async function settle() {
  for (let pass = 0; pass < 6; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
const settingOf = (names: string | string[], param: string) => (fixture(names, { now: NOW })["API-09"].items as Setting[]).find((row) => row.param === param)!;
type MountOptions = FixtureOptions & { path?: string; props?: Partial<SettingHistoryProps>; setting?: Partial<Setting>; param?: string; role?: "operator" | "viewer" };
async function mount({ path, props, setting, param = ENGAGE, role, ...options }: MountOptions = {}) {
  const api = startFixtureApi(options);
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
  const row = { ...settingOf(options.fixture ?? "in-band", param), ...setting };
  const onReverted = vi.fn();
  const view = renderWithApi(<><SettingHistoryPanel setting={row} manual onReverted={onReverted} {...props} /><Here /></>, role === "viewer" ? { role: "viewer", writesEnabled: true } : OPERATOR, path ?? `/settings?edit=${param}`);
  await settle();
  return { api, onReverted, view, row };
}
const windows = (api: FixtureApi) => requests(api, "API-12").map((row) => {
  const query = new URL(row.path, "http://dashboard.invalid").searchParams;
  return [query.get("from"), query.get("to")];
});
const chips = () => [...document.querySelectorAll(".drawer-history .chips a")].map((chip) => [text(chip), chip.getAttribute("aria-current")]);
const plot = () => created.filter((item) => !item.destroyed).at(-1)!;
const labels = () => { plot().draw(); return plot().ctx.texts.map((item) => item.text.replaceAll(" ", " ")); };
const revertButton = () => screen.queryByRole("button", { name: /^Revert to / });
const words = (element: Element | null) => text(element).replaceAll(" ", " ");
const keyItems = () => [...document.querySelectorAll(".chart-key li")].map((item) => text(item));

beforeEach(() => { MockPlot.reset(); FakeResizeObserver.instances = []; });
afterEach(() => { cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style"); });

test("API-12 reads from the window's start to 12 h after now, 7 d by default; the chips stay in the URL beside edit", async () => {
  const { api } = await mount({ fixture: "setting-history" });
  expect(windows(api)).toEqual([[iso(S - 7 * DAY_S), iso(S + PLAN_AHEAD_S)]]);
  expect(chips()).toEqual([["24 h", null], ["7 d", "true"], ["30 d", null], ["90 d", null]]);
  expect(plot().opts.scales!.x).toMatchObject({ min: S - 7 * DAY_S, max: S + PLAN_AHEAD_S });
  fireEvent.click(screen.getByRole("link", { name: "30 d" }));
  await settle();
  expect(here()).toBe(`/settings?edit=${ENGAGE}&hist=30d`);
  expect(chips().find(([, current]) => current)?.[0]).toBe("30 d");
  expect(windows(api)[1]).toEqual([iso(S - 30 * DAY_S), iso(S + PLAN_AHEAD_S)]);
  expect(plot().scaleCalls.at(-1)).toMatchObject({ key: "x", min: S - 30 * DAY_S, max: S + PLAN_AHEAD_S });
  fireEvent.click(screen.getByRole("link", { name: "90 d" }));
  await settle();
  expect(windows(api)[2]).toEqual([iso(S - 90 * DAY_S), iso(S + PLAN_AHEAD_S)]);
  cleanup();
  stopFixtureApi();
  const direct = await mount({ fixture: "setting-history", path: `/settings?edit=${ENGAGE}&hist=24h` });
  expect(windows(direct.api)).toEqual([[iso(S - DAY_S), iso(S + PLAN_AHEAD_S)]]);
  cleanup();
  stopFixtureApi();
  const unknown = await mount({ fixture: "setting-history", path: `/settings?edit=${ENGAGE}&hist=1y` });
  expect(windows(unknown.api)).toEqual([[iso(S - 7 * DAY_S), iso(S + PLAN_AHEAD_S)]]);
  expect(chips().find(([, current]) => current)?.[0]).toBe("7 d");
});

test("a window chip starts the chart on that window, whatever was zoomed before", async () => {
  await mount({ fixture: "setting-history" });
  act(() => plot().setScale("x", { min: S - DAY_S, max: S }));
  expect(screen.getByRole("button", { name: "Reset zoom" })).toBeTruthy();
  fireEvent.click(screen.getByRole("link", { name: "30 d" }));
  await settle();
  expect(screen.queryByRole("button", { name: "Reset zoom" })).toBeNull();
  expect(plot().scales.x).toMatchObject({ min: S - 30 * DAY_S, max: S + PLAN_AHEAD_S });
});

test("the chart: the device line solid, requests dashed amber and labelled by who asked, the plan ahead and the bounds", async () => {
  // Over 24 h the plan has room for its label; over 7 d a crowded chart may drop it (the key and the table keep its words).
  await mount({ fixture: "setting-history", path: `/settings?edit=${ENGAGE}&hist=24h` });
  const drawn = labels();
  expect(drawn).toContain("Now");
  expect(drawn).toContain("1.55 kPa device");
  expect(drawn).toContain("jason · dashboard");
  expect(drawn).toContain("iris · AI");
  expect(drawn).toContain("1.70 kPa SUPERSEDED");
  // max 2.50 is above the y range and min 0.50 below it: edge labels at the right.
  expect(drawn).toContain("max 2.50 kPa ↑");
  expect(drawn).toContain("min 0.50 kPa ↓");
  const strokes = plot().ctx.strokes;
  expect(strokes.filter((stroke) => stroke.stroke === TOKENS["--c-setpoint"] && !stroke.dash.length).length).toBeGreaterThanOrEqual(3);
  expect(strokes.filter((stroke) => stroke.stroke === TOKENS["--c-requested"] && JSON.stringify(stroke.dash) === "[6,4]")).toHaveLength(2);
  expect(strokes.filter((stroke) => stroke.stroke === TOKENS["--c-bound"] && JSON.stringify(stroke.dash) === "[8,4]")).toHaveLength(1);
  expect(keyItems()).toEqual(["Device readback", "iris · AI", "jason · dashboard", "SUPERSEDED", "Min and max"]);
  expect(screen.queryByText(NO_READBACK_TEXT)).toBeNull();
});

test("the data table lists each step with who asked and whether the device followed", async () => {
  await mount({ fixture: "setting-history" });
  const details = document.querySelector<HTMLDetailsElement>("details.chart-table")!;
  expect(text(details.querySelector("summary"))).toBe("Data table · 6 rows");
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  await settle();
  expect(text(details.querySelector("caption")).replaceAll(" ", " ")).toBe("Setting steps · bounds min 0.50 kPa, max 2.50 kPa");
  const steps = [...details.querySelectorAll("tbody tr td:last-child")].map((cell) => words(cell));
  expect(steps).toEqual(expect.arrayContaining([
    "VPD Mister Engage device 1.60 kPa",
    "VPD Mister Engage requested 1.60 kPa · iris · AI · CONFIRMED",
    "VPD Mister Engage requested 1.55 kPa · jason · dashboard · CONFIRMED · “Vandas drying”",
  ]));
});

test("readback_via none draws no device line and says so; a read-only row and a viewer see the history with no Revert", async () => {
  await mount({ fixture: "setting-history", setting: { readback_via: "none" } });
  expect(screen.getByText(NO_READBACK_TEXT)).toBeTruthy();
  plot().draw();
  expect(plot().ctx.strokes.some((stroke) => stroke.stroke === TOKENS["--c-setpoint"])).toBe(false);
  expect(keyItems()).not.toContain("Device readback");
  cleanup();
  stopFixtureApi();
  const readOnly = await mount({ fixture: "setting-history", setting: { writable: false } });
  expect(keyItems()).toContain("Device readback");
  expect(revertButton()).toBeNull();
  expect(screen.queryByLabelText("Revert reason")).toBeNull();
  expect(requests(readOnly.api, "API-12")).toHaveLength(1);
  cleanup();
  stopFixtureApi();
  await mount({ fixture: "setting-history", role: "viewer" });
  expect(keyItems()).toContain("Device readback");
  expect(revertButton()).toBeNull();
  expect(screen.queryByLabelText("Revert reason")).toBeNull();
});

test("Revert is one tap: one API-10 with the previous value, the Manual box and the prefilled reason, then the drawer closes", async () => {
  const { api, onReverted } = await mount({ fixture: "setting-history" });
  expect(words(revertButton())).toBe("Revert to 1.60 kPa (iris, 11/02 07:02)");
  expect((screen.getByLabelText("Revert reason") as HTMLInputElement).value).toBe("Revert to 1.60 kPa (iris, 11/02 07:02)");
  fireEvent.click(revertButton()!);
  await settle();
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "PUT", operationId: "API-10", path: `/api/v1/settings/${ENGAGE}`, body: { value: 1.6, manual: true, reason: "Revert to 1.60 kPa (iris, 11/02 07:02)" } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(onReverted).toHaveBeenCalledOnce();
  // There is no dialog: nothing else to confirm.
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("Revert sends the drawer's Manual box as it stands; the reason can be edited or cleared; Enter in it presses nothing", async () => {
  const { api } = await mount({ fixture: "setting-history", props: { manual: false } });
  const reason = screen.getByLabelText("Revert reason") as HTMLInputElement;
  expect(fireEvent.keyDown(reason, { key: "Enter" })).toBe(false);
  fireEvent.change(reason, { target: { value: "Back to Iris's value" } });
  fireEvent.click(revertButton()!);
  await settle();
  expect(writes(api).map((row) => row.body)).toEqual([{ value: 1.6, manual: false, reason: "Back to Iris's value" }]);
  cleanup();
  stopFixtureApi();
  const cleared = await mount({ fixture: "setting-history" });
  fireEvent.change(screen.getByLabelText("Revert reason"), { target: { value: "  " } });
  fireEvent.click(revertButton()!);
  await settle();
  expect(writes(cleared.api).map((row) => row.body)).toEqual([{ value: 1.6, manual: true }]);
});

test("a double tap sends once", async () => {
  const { api } = await mount({ fixture: "setting-history" });
  const button = revertButton()!;
  fireEvent.click(button);
  fireEvent.click(button);
  await settle();
  expect(writes(api)).toHaveLength(1);
});

test("with setting-history-release, Revert reads Revert to auto and sends one API-11 with its reason", async () => {
  const { api, onReverted } = await mount({ fixture: "setting-history-release" });
  expect(text(revertButton())).toBe("Revert to auto");
  fireEvent.click(revertButton()!);
  await settle();
  expect(writes(api)).toEqual([expect.objectContaining({ method: "DELETE", operationId: "API-11", path: `/api/v1/settings/${ENGAGE}?reason=Revert+to+auto+%28jason%2C+11%2F02+07%3A02%29`, body: null })]);
  expect(onReverted).toHaveBeenCalledOnce();
});

test("a release ends the step before it with Released; with no step before it there is nothing to end", async () => {
  await mount({ fixture: "setting-history-release", path: `/settings?edit=${ENGAGE}&hist=24h` });
  expect(labels()).toContain("jason · dashboard");
  expect(labels()).not.toContain("Released");
  cleanup();
  stopFixtureApi();
  // A value, then a release: the value's step ends at the release, labelled there.
  await mount({ fixture: "setting-history" });
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init?: RequestInit) => {
    const response = await fixtureFetch(input, init);
    if (!input.includes("/history")) return response;
    const body = await response.json() as { desired: { value: number | null; state: string }[] };
    body.desired[1] = { ...body.desired[1], value: null, state: "released" };
    return { ...response, json: async () => body } as Response;
  });
  fireEvent.click(screen.getByRole("link", { name: "24 h" }));
  await settle();
  expect(labels()).toContain("Released");
  expect(labels()).toContain("iris · AI");
});

test("when the window holds only the newest row, Revert reads 90 days back once and goes back to the row it finds", async () => {
  const { api } = await mount({ fixture: "setting-history-older" });
  expect(windows(api)).toEqual([[iso(S - 7 * DAY_S), iso(S + PLAN_AHEAD_S)], [iso(S - 90 * DAY_S), iso(S + PLAN_AHEAD_S)]]);
  expect(words(revertButton())).toBe("Revert to 1.60 kPa (steward, 10/21 08:02)");
  // The chart stays on the chosen window.
  expect(labels()).not.toContain("steward · agent · AI");
  await advance(120_000);
  expect(requests(api, "API-12")).toHaveLength(2);
  fireEvent.click(revertButton()!);
  await settle();
  expect(writes(api).map((row) => row.body)).toEqual([expect.objectContaining({ value: 1.6, manual: true })]);
});

test("with still no earlier row Revert releases to auto; the 90 d window needs no second read", async () => {
  const param = "fog_rh_ceiling_pct";
  const { api } = await mount({ param });
  expect(windows(api)).toEqual([[iso(S - 7 * DAY_S), iso(S + PLAN_AHEAD_S)], [iso(S - 90 * DAY_S), iso(S + PLAN_AHEAD_S)]]);
  expect(text(revertButton())).toBe("Revert to auto");
  fireEvent.click(revertButton()!);
  await settle();
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-11"]);
  cleanup();
  stopFixtureApi();
  const direct = await mount({ param, path: `/settings?edit=${param}&hist=90d` });
  expect(windows(direct.api)).toEqual([[iso(S - 90 * DAY_S), iso(S + PLAN_AHEAD_S)]]);
  expect(text(revertButton())).toBe("Revert to auto");
});

test("a setting with no desired row has no Revert: Iris's plan values are not desired rows", async () => {
  const { api } = await mount({ path: `/settings?edit=${ENGAGE}&hist=24h` });
  expect(labels()).toContain("1.70 kPa PLANNED · AI");
  expect(revertButton()).toBeNull();
  expect(screen.queryByLabelText("Revert reason")).toBeNull();
  expect(requests(api, "API-12")).toHaveLength(1);
});

/** Answers the first matching request with `status` and a Problem, and lets the rest through. */
function refuse(match: (input: string, method: string) => boolean, status: number, problem: object) {
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    if (!match(input, (init.method ?? "GET").toUpperCase())) return fixtureFetch(input, init);
    return { status, ok: false, type: "basic", headers: new Headers(), json: async () => ({ type: "about:blank", title: "Refused", status, retryable: false, ...problem }), text: async () => "" } as unknown as Response;
  });
}

test("a 422 shows in words beside the button and sends nothing else; the drawer stays open", async () => {
  const { api, onReverted } = await mount({ fixture: "setting-history" });
  refuse((_input, method) => method === "PUT", 422, { code: "out_of_range", field: "value", min: 1.7, max: 2.5 });
  fireEvent.click(revertButton()!);
  await settle();
  const button = revertButton()!;
  const error = document.querySelector(".drawer-revert .field-error")!;
  expect(words(error)).toBe("Must be 1.70–2.50 kPa");
  expect(button.getAttribute("aria-describedby")).toBe(error.id);
  expect(onReverted).not.toHaveBeenCalled();
  await advance(60_000);
  expect(writes(api)).toEqual([]);
});

test("bounds moved so the previous value is outside them: refused the same way, with nothing sent", async () => {
  const { api } = await mount({ fixture: "setting-history", setting: { min: 1.7 } });
  fireEvent.click(revertButton()!);
  await settle();
  expect(words(document.querySelector(".drawer-revert .field-error"))).toBe("Must be 1.70–2.50 kPa");
  expect(writes(api)).toEqual([]);
});

test("with changes switched off Revert and its reason are disabled; while Save is out Revert waits", async () => {
  startFixtureApi({ fixture: "setting-history" });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  renderWithApi(<SettingHistoryPanel setting={settingOf("setting-history", ENGAGE)} manual onReverted={() => {}} />, { role: "operator", writesEnabled: false }, `/settings?edit=${ENGAGE}`);
  await settle();
  expect(revertButton()!.hasAttribute("disabled")).toBe(true);
  expect((screen.getByLabelText("Revert reason") as HTMLInputElement).disabled).toBe(true);
  cleanup();
  stopFixtureApi();
  await mount({ fixture: "setting-history", props: { busy: true } });
  expect(revertButton()!.hasAttribute("disabled")).toBe(true);
});

test("a failed read shows Retry in this panel only, keeps the chips and offers no Revert", async () => {
  const { api } = await mount({ fixture: "setting-history", fail: { "API-12": 503 } });
  const panel = document.querySelector(".drawer-history")!;
  expect(within(panel as HTMLElement).getByText("Couldn't load history.")).toBeTruthy();
  expect(chips()).toHaveLength(4);
  expect(revertButton()).toBeNull();
  // Retry reads again: a button of its own, so it can never press the drawer's Save.
  const retry = within(panel as HTMLElement).getByRole("button", { name: "Retry" });
  expect(retry.getAttribute("type")).toBe("button");
  fireEvent.click(retry);
  await settle();
  expect(requests(api, "API-12")).toHaveLength(2);
});

test("a failed 90-day read says so and offers Retry, and never guesses a release", async () => {
  const param = "fog_rh_ceiling_pct";
  const api = startFixtureApi();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  refuse((input) => input.includes("/history") && new URL(input, "http://dashboard.invalid").searchParams.get("from") === iso(S - 90 * DAY_S), 503, { code: "unavailable" });
  renderWithApi(<SettingHistoryPanel setting={settingOf("in-band", param)} manual onReverted={() => {}} />, OPERATOR, `/settings?edit=${param}`);
  await settle();
  expect(screen.getByText("Couldn't check the older history for Revert.")).toBeTruthy();
  expect(revertButton()).toBeNull();
  expect(writes(api)).toEqual([]);
});

test("a new desired row while the drawer is open reads the history again from then", async () => {
  const { api, view, row } = await mount({ fixture: "setting-history" });
  expect(requests(api, "API-12")).toHaveLength(1);
  // API-09's polls with nothing new read nothing here.
  await advance(90_000);
  expect(requests(api, "API-12")).toHaveLength(1);
  view.rerender(<><SettingHistoryPanel setting={{ ...row, desired: { ...row.desired!, id: row.desired!.id + 1 } }} manual onReverted={() => {}} /><Here /></>);
  await settle();
  expect(windows(api)[1]).toEqual([iso(S + 90 - 7 * DAY_S), iso(S + 90 + PLAN_AHEAD_S)]);
});

test("the changes in this window read in words, newest first: from, to, who, manual, the reason and the readback state with its time", async () => {
  await mount({ fixture: "setting-history" });
  expect(text(document.querySelector(".drawer-history h3"))).toBe("Change history");
  expect([...document.querySelectorAll(".ctl-change")].map((entry) => words(entry))).toEqual([
    // With no shell here API-01 is not read, so the signed-in person reads by username.
    "1.60 kPa →to 1.55 kPa · jason · manual · “Vandas drying” · Confirmed 08:01",
    "1.60 kPa · iris · AI · Confirmed 07:03",
  ]);
  expect(document.querySelector(".ctl-change [data-badge=ai]")!.className).toBe("ctl-mark");
  cleanup();
  stopFixtureApi();
  // A release reads as one, with no value; a setting with no desired row in the window says so.
  await mount({ fixture: "setting-history-release" });
  expect([...document.querySelectorAll(".ctl-change")].map((entry) => words(entry))).toEqual([
    "1.55 kPa · jason · manual · “Vandas drying” · Confirmed 08:01",
    "Released to auto · jason · “Back to the plan” · Released 07:02",
  ]);
  cleanup();
  stopFixtureApi();
  await mount({ fixture: "in-band" });
  expect(text(document.querySelector(".ctl-changes .meta"))).toBe("No change was asked for in this window.");
});
