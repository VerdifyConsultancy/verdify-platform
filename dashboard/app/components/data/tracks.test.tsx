// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// StatusStrip, TimeTrack, MetricTile and DataList (redesign §4.2, §4.6): every state at its height, words for every
// colour, marks reached by arrow keys from one tab stop, neutral while the API is down and no style attribute served.
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { SessionProvider } from "../../lib/session";
import { localDayWindow } from "../../lib/time-scale";
import { renderUi, text } from "../test-render";
import { DataList } from "./data-list";
import { MetricTile, type TileState } from "./metric-tile";
import { StatusStrip, alertsLevel, inBandLevel, missedLevel, type StripCell } from "./status-strip";
import { TimeTrack, type TrackRow } from "./time-track";
import type { VizState } from "./viz";

afterEach(cleanup);
const STATES: readonly VizState[] = ["ready", "loading", "empty", "stale", "unknown", "error"];
const markup = (ui: ReactElement, apiDown = false) => renderToStaticMarkup(<MemoryRouter><SessionProvider apiDown={apiDown}>{ui}</SessionProvider></MemoryRouter>);
const GREEN = ".tag-ok, .status-complete, .fresh-ok";

// ---------------------------------------------------------------------------------------------------- StatusStrip
const CELLS: StripCell[] = [
  { key: "2026-10-31", level: 0, label: "Oct 31: temperature in band 97%", href: "/graphs?from=2026-10-31&to=2026-10-31" },
  { key: "2026-11-01", level: 2, label: "Nov 1: temperature in band 82%", href: "/graphs?from=2026-11-01&to=2026-11-01" },
  { key: "2026-11-02", level: null, label: "Nov 2: no data", href: "/graphs?from=2026-11-02&to=2026-11-02" },
  { key: "2026-11-03", level: 1, label: "Nov 3, so far: temperature in band 90%", partial: true, href: "/graphs?from=2026-11-03&to=2026-11-03", current: true },
];

test("the strip's levels follow §5.7: time in band, Iris missed and alerts raised", () => {
  expect([95, 94.9, 85, 84.9, 70, 69.9, null].map(inBandLevel)).toEqual([0, 1, 1, 2, 2, 3, null]);
  expect([0, 1, 2, 5, null].map(missedLevel)).toEqual([0, 2, 3, 3, null]);
  expect([0, 1, 2, 3, 9, 10, null].map(alertsLevel)).toEqual([0, 1, 1, 2, 2, 3, null]);
});

test("each cell is a link named in words with its level; no data hatched (none), never good; a partial day outlined; one tab stop", () => {
  const { container } = renderUi(<StatusStrip label="Temperature in band, day by day" cells={CELLS} legend />);
  const region = screen.getByRole("region", { name: "Temperature in band, day by day" });
  expect(region.getAttribute("tabindex")).toBe("0");
  const links = within(region).getAllByRole("link");
  expect(links.map((link) => [link.getAttribute("aria-label"), link.getAttribute("data-level"), link.getAttribute("tabindex")])).toEqual([
    ["Oct 31: temperature in band 97%", "0", "-1"], ["Nov 1: temperature in band 82%", "2", "-1"], ["Nov 2: no data", "none", "-1"],
    ["Nov 3, so far: temperature in band 90%", "1", "-1"],
  ]);
  expect(links[3].getAttribute("data-partial")).toBe("true");
  expect(links[3].getAttribute("aria-current")).toBe("date");
  expect(links[0].getAttribute("href")).toBe("/graphs?from=2026-10-31&to=2026-10-31");
  expect(text(container.querySelector(".strip-legend"))).toBe("goodslightmoderateseveretoday, partialno data");
  // Arrow keys move along the cells from the region; Home and End jump.
  region.focus();
  fireEvent.keyDown(region, { key: "ArrowRight" });
  expect(document.activeElement).toBe(links[0]);
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  expect(document.activeElement).toBe(links[1]);
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(document.activeElement).toBe(links[3]);
  fireEvent.keyDown(document.activeElement!, { key: "Home" });
  expect(document.activeElement).toBe(links[0]);
});

test("a strip of buttons selects a day; every state keeps the row's height; neutral with the API down", () => {
  const onSelect = vi.fn();
  renderUi(<StatusStrip label="Week" cells={[{ key: "mon", level: 0, label: "Mon 3: 5 ran ok", onSelect }]} />);
  fireEvent.click(screen.getByRole("button", { name: "Mon 3: 5 ran ok" }));
  expect(onSelect).toHaveBeenCalledTimes(1);
  cleanup();
  for (const state of STATES) {
    const { container } = renderUi(<StatusStrip label="Days" cells={CELLS} state={state} />);
    const drawn = state === "ready" || state === "stale";
    expect(container.querySelectorAll(".strip-cell"), state).toHaveLength(drawn ? 4 : 0);
    if (!drawn) expect(text(container.querySelector(".strip-empty")), state).toMatch(/^(Loading|No data|Unknown|Unavailable)$/);
    expect(container.querySelector(".strip-scroll")?.className.includes("viz-skeleton"), state).toBe(state === "loading");
    expect(container.querySelector(".strip")?.className.includes("is-stale") ?? false, state).toBe(state === "stale");
    expect(markup(<StatusStrip label="Days" cells={CELLS} state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
  const down = renderUi(<StatusStrip label="Days" cells={CELLS} />, { apiDown: true });
  expect(down.container.querySelector(".strip")?.className).toContain("is-neutral");
  expect(down.container.querySelector(GREEN)).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- TimeTrack
const DAY = localDayWindow("2026-11-02");
const at = (hhmm: string) => DAY.from + (Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3))) * 60;
const ROWS: TrackRow[] = [
  {
    key: "L01", label: "L01 Wall drip", data: { "data-line": "L01" }, end: <span>22 / 60 min</span>,
    spans: [
      { key: "a", start: at("07:31"), end: at("07:43"), status: "done", token: "eq-water", label: "07:31 ok", name: "L01 Wall drip, Mon 07:31, 12 min, done, ok", onSelect: vi.fn() },
      { key: "b", start: at("16:00"), end: at("16:10"), status: "planned", token: "eq-water", label: "16:00 10 min", href: "/water/schedules?run=b" },
    ],
    markers: [{ key: "plan", at: at("13:00"), label: "13:00 engage 1.60 → 1.70 (AI)" }],
  },
  { key: "L07", label: "L07 South mister", muted: true, note: "paused until Sat 09:00 · jason", spans: [{ key: "c", start: at("15:00"), end: at("15:03"), status: "skipped", label: "skipped · RH 92%" }] },
];

test("late in the window a marker's words and a narrow span's sit before them, so the track never cuts them off", () => {
  const { container } = renderUi(<TimeTrack label="Running and next" from={DAY.from} to={DAY.to} rows={[{
    key: "plan", label: "Iris, plan",
    spans: [{ key: "late", start: at("22:30"), end: at("22:40"), status: "planned", token: "eq-water", label: "22:30 10 min" }],
    markers: [{ key: "sunset", at: at("22:00"), label: "Sunset 22:00" }, { key: "noon", at: at("12:00"), label: "Noon" }],
  }]} />);
  const markers = [...container.querySelectorAll(".tt-marker")];
  expect(markers.map((marker) => [text(marker), marker.classList.contains("tt-marker-end")])).toEqual([["Sunset 22:00", true], ["Noon", false]]);
  expect(container.querySelector(".tt-span")?.className).toBe("tt-span tt-planned tt-eq-water tt-narrow tt-narrow-end");
});

test("a label stops where its room on the row ends (the next span, marker or the now rule), and below a stub it is read, not drawn", () => {
  const { container } = renderUi(<TimeTrack label="Running and next" from={DAY.from} to={DAY.to} now={at("10:42")} rows={[{
    key: "plan", label: "Plan", spans: [],
    markers: [
      { key: "a", at: at("13:00"), label: "13:00 VPD Mister Engage 1.60 → 1.70 kPa · AI" },
      { key: "b", at: at("16:30"), label: "16:30 Mister Pulse Gap 45.0 → 55.0 s · AI" },
      { key: "c", at: at("10:30"), label: "SUNRISE" },
    ],
  }, {
    key: "L04", label: "L04 Center mister",
    spans: [
      { key: "skip", start: at("09:47"), end: at("09:48"), status: "skipped", label: "09:47 skipped" },
      { key: "next", start: at("11:47"), end: at("11:52"), status: "planned", label: "11:47" },
    ],
  }]} />);
  const room = (element: Element) => Number((element as HTMLElement).style.getPropertyValue("--tt-room"));
  const [first, second, sunrise] = [...container.querySelectorAll(".tt-marker")];
  // 13:00's words run to 16:30's marker; 16:30's to the window's end; SUNRISE has 12 minutes before the now rule.
  expect(room(first)).toBeCloseTo((3.5 / 24) * 100, 3);
  expect(room(second)).toBeCloseTo((7.5 / 24) * 100, 3);
  expect(room(sunrise)).toBeCloseTo((0.2 / 24) * 100, 3);
  expect(first.querySelector(".tt-marker-label")?.getAttribute("title")).toBe("13:00 VPD Mister Engage 1.60 → 1.70 kPa · AI");
  expect(first.querySelector(".tt-marker-label")?.classList.contains("screen-reader")).toBe(false);
  // Too little room for a stub: the words stay in the row for a screen reader only.
  expect(sunrise.querySelector(".tt-marker-label")?.className).toBe("tt-marker-label screen-reader");
  expect(text(sunrise)).toBe("SUNRISE");
  // The skipped run's words would cross the now rule 54 minutes on: they are left out, its name keeps them.
  const skipped = container.querySelector(".tt-skipped")!;
  expect(room(skipped)).toBeCloseTo((54 / 60 / 24) * 100, 3);
  expect(skipped.querySelector(".tt-span-label")).toBeNull();
  expect(skipped.getAttribute("title")).toBe("09:47 skipped, skipped");
  const planned = container.querySelector(".tt-planned")!;
  expect(text(planned.querySelector(".tt-span-label"))).toBe("11:47");
  expect(room(planned)).toBeCloseTo(((24 - (11 * 60 + 52) / 60) / 24) * 100, 3);
});

test("ticks centre on their hour with a mark there, keep inside the window only at its ends, and give way to the now rule's name", () => {
  const { container } = renderUi(<TimeTrack label="Today" from={DAY.from} to={DAY.to} now={at("10:42")} rows={ROWS} />);
  const ticks = [...container.querySelectorAll(".tt-tick")];
  expect(ticks.map((tick) => [text(tick), tick.classList.contains("tt-tick-start"), tick.classList.contains("tt-tick-end")])).toEqual([
    ["00:00", true, false], ["03:00", false, false], ["06:00", false, false], ["09:00", false, false], ["12:00", false, false],
    ["15:00", false, false], ["18:00", false, false], ["21:00", false, false], ["00:00", false, true],
  ]);
  // 12:00 sits under "now 10:42": its words give way, its mark stays.
  expect(ticks.map((tick) => tick.querySelector(".tt-tick-quiet") !== null)).toEqual([false, false, false, false, true, false, false, false, false]);
  expect(text(container.querySelector(".tt-now-label"))).toBe("now 10:42");
  expect(Number((container.querySelector(".tt-now-label") as HTMLElement).style.getPropertyValue("--tt-at"))).toBeCloseTo(((10 * 60 + 42) / 1440) * 100, 3);
  // No now inside the window, no name.
  cleanup();
  const later = renderUi(<TimeTrack label="Tomorrow" from={DAY.from} to={DAY.to} now={DAY.to + 3600} rows={ROWS} />);
  expect(later.container.querySelector(".tt-now-label")).toBeNull();
});

test("rows are a list with their labels and ends; a span with detail is a button or link named in words, reached by arrow keys", () => {
  const select = ROWS[0].spans[0].onSelect as ReturnType<typeof vi.fn>;
  const { container } = renderUi(<TimeTrack label="Today's runs" from={DAY.from} to={DAY.to} rows={ROWS} now={at("10:42")} night={[{ from: DAY.from, to: at("06:31") }, { from: at("16:52"), to: DAY.to }]} />);
  const track = screen.getByRole("group", { name: "Today's runs" });
  expect(track.getAttribute("tabindex")).toBe("0");
  const rows = within(track).getAllByRole("listitem");
  expect(rows.map((row) => text(row.querySelector(".tt-label")))).toEqual(["L01 Wall drip", "L07 South mister"]);
  expect(rows[0].getAttribute("data-line")).toBe("L01");
  expect(rows[1].className).toBe("tt-row tt-row-muted");
  expect(text(rows[0].querySelector(".tt-end"))).toBe("22 / 60 min");
  const run = screen.getByRole("button", { name: "L01 Wall drip, Mon 07:31, 12 min, done, ok" });
  expect(run.className).toBe("tt-span tt-done tt-eq-water tt-narrow");
  expect(run.getAttribute("tabindex")).toBe("-1");
  fireEvent.click(run);
  expect(select).toHaveBeenCalledWith(run);
  expect(screen.getByRole("link", { name: "16:00 10 min, planned" }).getAttribute("href")).toBe("/water/schedules?run=b");
  // The skipped span has no detail: words, no control.
  expect(container.querySelector(".tt-skipped")?.tagName).toBe("SPAN");
  expect(text(container.querySelector(".tt-note"))).toBe("paused until Sat 09:00 · jason");
  // Night, the now rule on each row and the marker are drawn; the axis has every 3 h, the 6 h ones kept on phones.
  expect(container.querySelectorAll(".tt-night")).toHaveLength(4);
  expect(container.querySelectorAll(".tt-now")).toHaveLength(2);
  expect(text(container.querySelector(".tt-marker-label"))).toBe("13:00 engage 1.60 → 1.70 (AI)");
  expect(container.querySelector(".tt-marker")?.classList.contains("tt-marker-end")).toBe(false);
  expect([...container.querySelectorAll(".tt-tick")].map((tick) => [text(tick), tick.classList.contains("tt-tick-wide")])).toEqual([
    ["00:00", false], ["03:00", true], ["06:00", false], ["09:00", true], ["12:00", false], ["15:00", true], ["18:00", false], ["21:00", true], ["00:00", false],
  ]);
  track.focus();
  fireEvent.keyDown(track, { key: "ArrowRight" });
  expect(document.activeElement).toBe(run);
  // Positions are CSSOM custom properties: 07:31 is 31.3% of the day.
  expect(Number((run as HTMLElement).style.getPropertyValue("--tt-start"))).toBeCloseTo(((7 * 60 + 31) / 1440) * 100, 3);
});

test("TimeTrack in every state keeps its frame; served markup has no style attribute; neutral with the API down", () => {
  for (const state of STATES) {
    const { container } = renderUi(<TimeTrack label="Runs" from={DAY.from} to={DAY.to} rows={ROWS} state={state} />);
    const drawn = state === "ready" || state === "stale";
    expect(container.querySelectorAll(".tt-row"), state).toHaveLength(drawn ? 2 : 0);
    expect(container.querySelector(".tt")?.className.includes("viz-skeleton"), state).toBe(state === "loading");
    expect(container.querySelector(".tt")?.className.includes("is-stale"), state).toBe(state === "stale");
    // A track with no rows keeps its axis row's height and names no time, so its server render is the same at any clock.
    expect(container.querySelectorAll(".tt-axis"), state).toHaveLength(1);
    expect(container.querySelectorAll(".tt-tick"), state).toHaveLength(drawn ? 9 : 0);
    expect(markup(<TimeTrack label="Runs" from={DAY.from} to={DAY.to} rows={ROWS} state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
  const down = renderUi(<TimeTrack label="Runs" from={DAY.from} to={DAY.to} rows={ROWS} />, { apiDown: true });
  expect(down.container.querySelector(".tt")?.className).toContain("is-neutral");
  expect(down.container.querySelector(GREEN)).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- MetricTile
test("MetricTile in every state at its height: the value and delta, a skeleton, one empty line, STALE, its own error with Retry", () => {
  const retry = vi.fn();
  const states: TileState[] = ["ready", "loading", "empty", "stale", "error"];
  for (const state of states) {
    const { container } = renderUi(<MetricTile label="Temperature in band" value="94.2%" delta={<span>▲ 1.3 pts</span>} visual={<span className="viz" />} meta="30 days" state={state} onRetry={retry} data={{ "data-tile": "temp" }} />);
    const tile = screen.getByRole("article", { name: "Temperature in band" });
    expect(tile.getAttribute("data-tile")).toBe("temp");
    expect(tile.className).toBe("card metric-tile");
    if (state === "ready" || state === "stale") {
      expect(text(container.querySelector(".metric-value")), state).toBe(state === "stale" ? "94.2% Stale" : "94.2%");
      expect(container.querySelector(".metric-value")?.className.includes("value-muted"), state).toBe(state === "stale");
      expect(text(container.querySelector(".metric-meta"))).toBe("30 days");
    }
    if (state === "loading") expect(tile.getAttribute("aria-busy"), state).toBe("true");
    if (state === "empty") expect(text(container.querySelector(".metric-empty"))).toBe("No data in this period");
    if (state === "error") {
      expect(text(container.querySelector(".metric-error p"))).toBe("Couldn't load temperature in band.");
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(retry).toHaveBeenCalledTimes(1);
    }
    expect(markup(<MetricTile label="L" value="1" state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
});

test("MetricTile: unknown is — with its reason, never 0; the label links to the detail; the tone drops while the API is down", () => {
  renderUi(<MetricTile label="Water" value={null} reason="Not served before Nov 1" href="/water/history" tone="warn" />);
  expect(text(document.querySelector(".metric-value"))).toBe("— Not served before Nov 1");
  expect(screen.getByRole("link", { name: "Water" }).getAttribute("href")).toBe("/water/history");
  expect(document.querySelector(".metric-tile")?.className).toBe("card metric-tile tone-warn");
  // The label is the eyebrow that names the tile, never a heading.
  expect(screen.queryByRole("heading")).toBeNull();
  cleanup();
  renderUi(<MetricTile label="Water" value="1" tone="warn" />, { apiDown: true });
  expect(document.querySelector(".metric-tile")?.className).toBe("card metric-tile");
});

// ---------------------------------------------------------------------------------------------------- DataList
test("DataList: term and value pairs; a missing value is — with its reason, never 0 or blank", () => {
  const { container } = renderUi(<DataList inline items={[{ term: "Flow", value: "none" }, { term: "Occupancy", value: null, reason: "no camera" }, { term: "Fallback", value: Number.NaN }]} />);
  expect(container.querySelector("dl")?.className).toBe("data-list data-list-inline");
  expect([...container.querySelectorAll(".data-pair")].map(text)).toEqual(["Flownone", "Occupancy— no camera", "Fallback—"]);
  expect(markup(<DataList items={[{ term: "a", value: 1 }]} />)).not.toMatch(/\sstyle=/);
});
