// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The crop-band curve preview (#1139) against #1122's fixture API in process, with uPlot mocked: the debounce, the curve
// straight from API-19, the 422 and any other failure. The clock is 10:42 America/Denver on 2026-11-02 (MST).
import { act, cleanup, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import { FakeResizeObserver, MockPlot, TOKENS, created, media, setTokens } from "../charts/fixtures/uplot-mock";
import type { BandPreview, Setting } from "../lib/operator-api";
import { NOW, OPERATOR, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, writes, type FixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { BandPreviewPanel, PREVIEW_DEBOUNCE_MS, PREVIEW_UNAVAILABLE, PREVIEW_WORD, hasBandCurve } from "./band-preview";
import { text } from "./test-render";

vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const CENTER = "zone_vpd_target_center_sm";
const rows = (names: string = "in-band") => fixture(names, { now: NOW })["API-09"].items as Setting[];
const settingOf = (param: string) => rows().find((row) => row.param === param)!;
async function settle() {
  for (let pass = 0; pass < 6; pass++) {
    await advance(10);
    await act(async () => { await vi.dynamicImportSettled(); });
  }
}
async function mount(param: string, draft: number | null, options: FixtureOptions = {}) {
  const api = startFixtureApi({ fixture: "band-preview-center", ...options });
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("matchMedia", media({ fine: true }));
  setTokens();
  const setting = settingOf(param);
  const view = renderWithApi(<BandPreviewPanel setting={setting} draft={draft} />, OPERATOR);
  await settle();
  const redraft = async (next: number | null, wait = PREVIEW_DEBOUNCE_MS) => {
    view.rerender(<BandPreviewPanel setting={setting} draft={next} />);
    await advance(wait);
    await settle();
  };
  return { api, view, redraft };
}
const asked = (api: FixtureApi) => requests(api, "API-19").map((row) => {
  const query = new URL(row.path, "http://dashboard.invalid").searchParams;
  return { param: query.get("param"), value: query.get("value"), day: query.get("day") };
});
const plot = () => created.filter((item) => !item.destroyed).at(-1)!;
const labels = () => { plot().draw(); return plot().ctx.texts.map((item) => item.text); };
const message = () => document.querySelector(".band-preview .chart-message");
const words = (element: Element | null) => text(element).replaceAll(" ", " ");

const { createObjectURL, revokeObjectURL } = URL;
beforeEach(() => {
  MockPlot.reset(); FakeResizeObserver.instances = [];
  // jsdom has no Blob URLs.
  URL.createObjectURL = vi.fn(() => "blob:csv");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => {
  cleanup(); stopFixtureApi(); document.documentElement.removeAttribute("style");
  URL.createObjectURL = createObjectURL; URL.revokeObjectURL = revokeObjectURL;
});

test("a curve shows for every anchor and width, and for no other crop_band or registry row", () => {
  const curved = rows().filter(hasBandCurve).map((row) => row.param);
  expect(curved).toHaveLength(48);
  const anchors = ["sr", "sm", "ss", "mid"];
  const house = ["temp_low", "temp_target", "temp_high", "vpd_low", "vpd_target", "vpd_high"].flatMap((series) => anchors.map((anchor) => `band_${series}_${anchor}`));
  const zones = ["center", "south", "west", "east"];
  const zone = zones.flatMap((name) => [...anchors.map((anchor) => `zone_vpd_target_${name}_${anchor}`), `zone_vpd_width_below_${name}`, `zone_vpd_width_above_${name}`]);
  expect(new Set(curved)).toEqual(new Set([...house, ...zone]));
  for (const param of ["zone_priority_center", "wet_taper_before_sunset_min", "manual_override_timeout_min", "temp_low", "vpd_target_center", "band_house_temp_low", "mister_engage_kpa"]) {
    expect(hasBandCurve(settingOf(param)), param).toBe(false);
  }
});

test("the value on opening reads at once; each change reads 300 ms after the last one, with no day", async () => {
  const { api, view } = await mount(CENTER, 1.1);
  expect(asked(api)).toEqual([{ param: CENTER, value: "1.1", day: null }]);
  const setting = settingOf(CENTER);
  view.rerender(<BandPreviewPanel setting={setting} draft={1.2} />);
  await advance(PREVIEW_DEBOUNCE_MS - 50);
  view.rerender(<BandPreviewPanel setting={setting} draft={1.3} />);
  await advance(PREVIEW_DEBOUNCE_MS - 50);
  expect(asked(api)).toHaveLength(1);
  await advance(50);
  await settle();
  expect(asked(api).map((row) => row.value)).toEqual(["1.1", "1.3"]);
  expect(requests(api).filter((row) => row.operationId === "API-19")[1].path).toBe(`/api/v1/band/preview?param=${CENTER}&value=1.3`);
  // A change that comes back before the wait is over reads nothing new.
  view.rerender(<BandPreviewPanel setting={setting} draft={1.4} />);
  await advance(100);
  view.rerender(<BandPreviewPanel setting={setting} draft={1.3} />);
  await advance(1_000);
  expect(asked(api)).toHaveLength(2);
  expect(writes(api)).toEqual([]);
});

test("it draws API-19's columns as served, over the local day: the ribbon, the solid target and the dashed draft labelled PREVIEW", async () => {
  const { api, redraft } = await mount(CENTER, 1.1);
  await redraft(1.25);
  const answer = api.respond({ url: `/v1/band/preview?param=${CENTER}&value=1.25` }).body as BandPreview;
  const chart = plot();
  // The browser computes no curve: x and each line are the server's.
  expect(chart.data[0]).toEqual(answer.t);
  expect(chart.data[1]).toEqual(answer.current.target);
  expect(chart.data[2]).toEqual(answer.draft.target);
  expect(chart.data[3]).toEqual(answer.draft.low);
  expect(chart.data[4]).toEqual(answer.draft.high);
  // 00:00 to 24:00 on the local day.
  expect(chart.opts.scales!.x).toMatchObject({ min: Date.parse("2026-11-02T07:00:00Z") / 1000 });
  expect(chart.scaleCalls.at(-1)).toMatchObject({ min: Date.parse("2026-11-02T07:00:00Z") / 1000, max: Date.parse("2026-11-03T07:00:00Z") / 1000 });
  expect(chart.opts.series.map((series) => [series.label, series.stroke, series.dash, series.spanGaps])).toEqual([
    [undefined, undefined, undefined, undefined],
    ["Target", TOKENS["--c-vpd"], undefined, false],
    [`${PREVIEW_WORD} target`, TOKENS["--c-requested"], [8, 4], false],
    [`${PREVIEW_WORD} low`, TOKENS["--c-requested"], [8, 4], false],
    [`${PREVIEW_WORD} high`, TOKENS["--c-requested"], [8, 4], false],
  ]);
  const drawn = labels();
  expect(drawn).toContain(PREVIEW_WORD);
  expect(drawn).toContain("Now");
  for (const mark of ["Sunrise", "Noon", "Sunset"]) expect(drawn).toContain(mark);
  const painted = chart.ctx;
  expect(painted.fills.some((fill) => fill.color === TOKENS["--c-band-wash"])).toBe(true);
  expect(painted.strokes.filter((stroke) => stroke.stroke === TOKENS["--c-band-edge"] && stroke.width === 1)).toHaveLength(2);
  expect(painted.strokes.some((stroke) => stroke.stroke === TOKENS["--ink"] && stroke.width === 1.5)).toBe(true);
  expect(painted.strokes.filter((stroke) => stroke.stroke === TOKENS["--muted"] && JSON.stringify(stroke.dash) === "[4,3]")).toHaveLength(3);
  expect(painted.depth).toBe(0);
  expect(painted.texts.find((item) => item.text === PREVIEW_WORD)).toMatchObject({ color: TOKENS["--warn-text"] });
  // The same words beside the chart: what it shows, the solar times and a table.
  expect([...document.querySelectorAll(".band-preview .chart-key li")].map(text)).toEqual(["Band as stored", "Target as stored", PREVIEW_WORD]);
  expect([...document.querySelectorAll(".band-preview .chart-notes li")].map(text)).toEqual(["Sunrise 06:35", "Solar noon 11:47", "Sunset 16:58"]);
  expect(text(document.querySelector(".band-preview figcaption"))).toBe("Center zone VPD · kPa");
  expect(text(document.querySelector(".band-preview details summary"))).toBe("Data table · 144 rows");
  expect(screen.getByRole("link", { name: "CSV of this window" })).toBeTruthy();
  expect(screen.getByRole("img").getAttribute("aria-label")).toContain("and dashed, the preview of this value");
  // A tooltip names both at the cursor.
  chart.fire("ready");
  chart.hover(answer.t[72], 72);
  const tooltip = [...chart.over.querySelectorAll(".chart-tooltip > div")].map((line) => words(line));
  expect(tooltip).toHaveLength(3);
  expect(tooltip[1]).toMatch(/^Band \d\.\d\d kPa–\d\.\d\d kPa · target \d\.\d\d kPa$/);
  expect(tooltip[2]).toMatch(/^PREVIEW \d\.\d\d kPa–\d\.\d\d kPa · target 1\.25 kPa$/);
});

test("a draft that changes nothing draws the band alone, with no PREVIEW", async () => {
  await mount(CENTER, 1.1);
  expect(labels()).not.toContain(PREVIEW_WORD);
  expect((plot().data[2] as (number | null)[]).every((value) => value === null)).toBe(true);
  expect([...document.querySelectorAll(".band-preview .chart-key li")].map(text)).toEqual(["Band as stored", "Target as stored"]);
  // A house temperature anchor reads in its own colour and unit.
  cleanup();
  stopFixtureApi();
  MockPlot.reset();
  await mount("band_temp_low_sm", settingOf("band_temp_low_sm").readback!.value);
  expect(text(document.querySelector(".band-preview figcaption"))).toBe("House temperature · °F");
  expect(plot().opts.series[1].stroke).toBe(TOKENS["--c-temp"]);
});

test("a width moves only its own edge, from the server's answer", async () => {
  const { redraft } = await mount("zone_vpd_width_below_center", 0.2);
  await redraft(0.3);
  const chart = plot();
  const [target, draftTarget, draftLow, draftHigh] = [chart.data[1], chart.data[2], chart.data[3], chart.data[4]] as (number | null)[][];
  expect(draftTarget).toEqual(target);
  expect((draftLow[72] as number) - (draftTarget[72] as number)).toBeCloseTo(-0.3, 5);
  expect(draftHigh[72]).toBeGreaterThan(draftTarget[72] as number);
  // PREVIEW sits on the edge that moved.
  chart.draw();
  const label = chart.ctx.texts.find((item) => item.text === PREVIEW_WORD)!;
  const onLine = (column: (number | null)[]) => column.some((value) => Math.abs(chart.valToPos(value as number, "y") - (label.y + 16)) < 1);
  expect([onLine(draftLow), onLine(draftTarget)]).toEqual([true, false]);
  expect(text(document.querySelector(".band-preview figcaption"))).toBe("Center zone VPD · kPa");
});

test("a draft outside the bounds reads Must be … in the panel, from the Problem; a good one brings the curve back", async () => {
  const { api, redraft } = await mount(CENTER, 1.1);
  await redraft(3.5);
  expect(asked(api).at(-1)).toMatchObject({ value: "3.5" });
  expect(words(message())).toBe("Must be 0.10–3.00 kPa");
  expect(within(document.querySelector<HTMLElement>(".band-preview")!).getByRole("status")).toBe(message()!.querySelector("p"));
  expect(screen.queryByRole("img")).toBeNull();
  await redraft(1.25);
  expect(message()).toBeNull();
  expect(labels()).toContain(PREVIEW_WORD);
  expect(writes(api)).toEqual([]);
});

test("any other failure reads Preview unavailable; unknown_target reads its words", async () => {
  await mount(CENTER, 1.1, { fail: { "API-19": 503 } });
  expect(text(message())).toBe(PREVIEW_UNAVAILABLE);
  cleanup();
  stopFixtureApi();
  await mount(CENTER, 1.1, {});
  const fixtureFetch = globalThis.fetch;
  vi.stubGlobal("fetch", async (input: string, init: RequestInit = {}) => {
    if (!input.includes("/band/preview")) return fixtureFetch(input, init);
    const problem = { type: "about:blank", title: "Not a crop-band anchor", status: 422, code: "unknown_target", retryable: false, field: "param" };
    return { status: 422, ok: false, type: "basic", headers: new Headers(), json: async () => problem, text: async () => "" } as unknown as Response;
  });
  cleanup();
  renderWithApi(<BandPreviewPanel setting={settingOf(CENTER)} draft={1.2} />, OPERATOR);
  await settle();
  expect(text(message())).toBe("Nothing by that name");
});

test("the marks are left off if the solar read fails, or names another day; the curve still draws", async () => {
  await mount(CENTER, 1.1, { fail: { "API-IRR-02": 503 } });
  const drawn = labels();
  for (const mark of ["Sunrise", "Noon", "Sunset"]) expect(drawn).not.toContain(mark);
  expect(drawn).toContain("Now");
  expect(document.querySelector(".band-preview .chart-notes")).toBeNull();
  expect(plot().data[1]).toHaveLength(144);
});

test("a value that is not a number reads nothing and draws no draft; before any answer it says what to do", async () => {
  const { api, redraft } = await mount(CENTER, 1.1);
  await redraft(1.25);
  await redraft(null);
  expect(asked(api)).toHaveLength(2);
  expect(labels()).not.toContain(PREVIEW_WORD);
  cleanup();
  stopFixtureApi();
  const empty = await mount(CENTER, null);
  expect(asked(empty.api)).toEqual([]);
  expect(text(message())).toBe("Enter a number to preview its curve");
});
