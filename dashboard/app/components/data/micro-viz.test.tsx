// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The micro-visualizations of redesign §4.6 (BandBar, Meter, Sparkline, Delta): every state at its reserved height,
// unknown hatched and never drawn as 0, stale muted, neutral while the API is down, and no style attribute in the markup.
import { cleanup, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test } from "vitest";
import { SessionProvider } from "../../lib/session";
import { NBSP, renderUi, text } from "../test-render";
import { BandBar, bandBarWords, bandPosition, bandScale } from "./band-bar";
import { Delta, deltaAmount } from "./delta";
import { Meter, meterWords } from "./meter";
import { Sparkline } from "./sparkline";
import type { VizState } from "./viz";

afterEach(cleanup);
const STATES: readonly VizState[] = ["ready", "loading", "empty", "stale", "unknown", "error"];
/** The server's markup, which the CSP and the a11y spec read: no style attribute, whatever the state. */
const markup = (ui: ReactElement, apiDown = false) => renderToStaticMarkup(<MemoryRouter><SessionProvider apiDown={apiDown}>{ui}</SessionProvider></MemoryRouter>);
const GREEN = ".tag-ok, .status-complete, .fresh-ok";
/** Words with the no-break space before a unit read as a plain space. */
const plain = (words: string | null | undefined) => (words ?? "").replaceAll(NBSP, " ");

// ---------------------------------------------------------------------------------------------------- BandBar
const temp = { label: "Temperature", unit: "°F", value: 64.8, band: { low: 62, high: 70 }, today: { low: 61.2, high: 71.9 } };

test("BandBar: the band plus half its width each side, widened for today and the value, never past 1.5 widths", () => {
  expect(bandScale(64.8, { low: 62, high: 70 }, { low: 61.2, high: 71.9 })).toEqual({ low: 58, high: 74 });
  expect(bandScale(64.8, { low: 62, high: 70 }, { low: 50, high: 71 })).toEqual({ low: 50, high: 74 });
  // A wild value cannot squash the band: the scale stops at 1.5 widths and the dot clamps with an arrowhead.
  expect(bandScale(120, { low: 62, high: 70 }, null)).toEqual({ low: 58, high: 82 });
  // RH has no band: the scale is today's range and the value, padded.
  const rh = bandScale(71, null, { low: 48, high: 82 })!;
  expect(rh.low).toBeLessThan(48);
  expect(rh.high).toBeGreaterThan(82);
  expect(bandScale(null, null, null)).toBeNull();
  expect([bandPosition(61, temp.band), bandPosition(64.8, temp.band), bandPosition(71, temp.band), bandPosition(null, temp.band)]).toEqual(["below", "in", "above", null]);
  expect(plain(bandBarWords(temp))).toBe("Temperature 64.8 °F, band 62.0–70.0 °F, today 61.2–71.9 °F");
  expect(plain(bandBarWords({ label: "RH", unit: "%", value: 71, band: null, today: { low: 48, high: 82 } }))).toBe("RH 71.0%, no band, today 48.0–82.0%");
});

test("BandBar in every state: ready draws band, today and dot; unknown hatches with no dot; stale is muted; loading reserves its height", () => {
  for (const state of STATES) {
    const { container } = renderUi(<BandBar {...temp} state={state} />);
    const svg = container.querySelector("svg");
    if (state === "loading") {
      expect(container.querySelector(".viz-skeleton.bb"), state).not.toBeNull();
      expect(svg, state).toBeNull();
    } else {
      expect(svg?.getAttribute("class"), state).toContain("bb");
      const drawn = state === "ready" || state === "stale";
      expect(container.querySelectorAll(".bb-dot"), state).toHaveLength(drawn ? 1 : 0);
      expect(container.querySelectorAll(".bb-band"), state).toHaveLength(drawn ? 1 : 0);
      expect(container.querySelectorAll(".bb-unknown"), state).toHaveLength(drawn ? 0 : 1);
      expect(svg?.getAttribute("class")?.includes("is-stale"), state).toBe(state === "stale");
    }
    expect(markup(<BandBar {...temp} state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
  // An unknown value is no dot, never a dot at 0.
  const { container } = renderUi(<BandBar {...temp} value={null} />);
  expect(container.querySelector(".bb-dot")).toBeNull();
  expect(container.querySelector(".bb-unknown")).not.toBeNull();
});

test("BandBar: decorative beside its numbers, else an image named by them; the hero variant; a clamp; neutral with the API down", () => {
  const { container } = renderUi(<BandBar {...temp} decorative={false} hero />);
  expect(plain(screen.getByRole("img").getAttribute("aria-label"))).toBe("Temperature 64.8 °F, band 62.0–70.0 °F, today 61.2–71.9 °F");
  expect(container.querySelector("svg")?.getAttribute("class")).toContain("bb-hero");
  cleanup();
  const clamped = renderUi(<BandBar {...temp} value={120} />);
  expect(clamped.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  expect(clamped.container.querySelector(".bb-clamp")).not.toBeNull();
  cleanup();
  const down = renderUi(<BandBar {...temp} />, { apiDown: true });
  expect(down.container.querySelector("svg")?.getAttribute("class")).toContain("is-neutral");
  expect(down.container.querySelector(GREEN)).toBeNull();
  // RH: no band segment.
  cleanup();
  expect(renderUi(<BandBar label="RH" unit="%" value={71} band={null} today={{ low: 48, high: 82 }} />).container.querySelector(".bb-band")).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- Meter
test("Meter words: the done part against the cap, cap reached, minutes over", () => {
  const words = (value: number | null, cap: number | null, unit: string) => { const said = meterWords(value, cap, unit); return [plain(said.amount), said.over && plain(said.over)]; };
  expect(words(12, 60, "min")).toEqual(["12 / 60 min", null]);
  expect(words(60, 60, "min")).toEqual(["60 / 60 min", "cap reached"]);
  expect(words(72, 60, "min")).toEqual(["72 / 60 min", "+12 min over"]);
  expect(words(null, 60, "min")).toEqual(["— / 60 min", null]);
  expect(words(21, 360, "gal")).toEqual(["21 / 360 gal", null]);
  expect(words(21, null, "gal")).toEqual(["21 gal", null]);
  // Minutes stay whole minutes past 2 h, as the caps are set, and reach or pass the cap as their whole minutes do.
  expect(words(132, 120, "min")).toEqual(["132 / 120 min", "+12 min over"]);
  expect(words(131.6, 120, "min")).toEqual(["132 / 120 min", "+12 min over"]);
  expect(words(59.7, 60, "min")).toEqual(["60 / 60 min", "cap reached"]);
  expect(words(6, null, "min")).toEqual(["6 min", null]);
  expect(words(null, 0, "min")).toEqual(["—", null]);
});

test("Meter in every state: the fill in its domain token, the planned outline and the ink cap; unknown hatched with no fill", () => {
  for (const state of STATES) {
    const { container } = renderUi(<Meter label="Wall minutes today" unit="min" value={12} planned={10} cap={60} domain="water" state={state} showWords />);
    if (state === "loading") expect(container.querySelector(".viz-skeleton.meter"), state).not.toBeNull();
    else {
      const meter = container.querySelector(".meter")!;
      const drawn = state === "ready" || state === "stale";
      expect(meter.className, state).toContain("meter-water");
      expect(container.querySelectorAll(".meter-fill"), state).toHaveLength(drawn ? 1 : 0);
      expect(container.querySelectorAll(".meter-planned"), state).toHaveLength(drawn ? 1 : 0);
      expect(container.querySelectorAll(".meter-cap"), state).toHaveLength(1);
      expect(meter.className.includes("is-unknown"), state).toBe(!drawn);
      expect(text(container.querySelector(".meter-words")), state).toBe(drawn ? "12 / 60 min" : "—");
    }
    expect(markup(<Meter label="Wall" unit="min" value={12} cap={60} state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
});

test("Meter: at or over its cap the fill stops at 100% and the tick turns fault; named in words unless the words sit beside it", () => {
  const { container } = renderUi(<Meter label="Center minutes today" unit="min" value={132} cap={120} domain="mister" />);
  expect(container.querySelector(".meter-fill")?.getAttribute("width")).toBe("100%");
  expect(container.querySelector(".meter")?.className).toContain("is-capped");
  expect(plain(screen.getByRole("img").getAttribute("aria-label"))).toBe("Center minutes today: 132 / 120 min, +12 min over");
  cleanup();
  const down = renderUi(<Meter label="SAF-4" unit="gal" value={21} cap={360} domain="water" showWords />, { apiDown: true });
  expect(down.container.querySelector(".meter")?.className).toContain("is-neutral");
  expect(down.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
  expect(down.container.querySelector(GREEN)).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- Sparkline
test("Sparkline: gaps break the line, the last point is a dot, the band sits behind; bars for counts", () => {
  const { container } = renderUi(<Sparkline values={[0.5, 0.6, null, 0.7, 0.8, 0.9]} token="vpd" band={{ low: 0.45, high: 0.8 }} label="VPD over 24 h" />);
  const svg = container.querySelector("svg")!;
  expect(svg.getAttribute("class")).toBe("spark spark-tile spark-vpd");
  expect(svg.getAttribute("height")).toBe("28");
  expect(svg.getAttribute("preserveAspectRatio")).toBe("none");
  expect(container.querySelectorAll(".spark-line")).toHaveLength(2);
  expect(container.querySelectorAll(".spark-dot")).toHaveLength(1);
  expect(container.querySelectorAll(".spark-band")).toHaveLength(1);
  expect(screen.getByRole("img").getAttribute("aria-label")).toBe("VPD over 24 h");
  cleanup();
  const bars = renderUi(<Sparkline values={[3, null, 5, 0]} kind="bars" token="water" size="row" />);
  expect(bars.container.querySelectorAll(".spark-bar")).toHaveLength(3);
  expect(bars.container.querySelector("svg")?.getAttribute("height")).toBe("20");
  expect(bars.container.querySelector("svg")?.getAttribute("aria-hidden")).toBe("true");
});

test("Sparkline in every state, and neutral with the API down", () => {
  for (const state of STATES) {
    const { container } = renderUi(<Sparkline values={[1, 2, 3]} token="temp" state={state} label="Temperature" />);
    if (state === "loading") expect(container.querySelector(".viz-skeleton.spark-tile"), state).not.toBeNull();
    else {
      const drawn = state === "ready" || state === "stale";
      expect(container.querySelectorAll(".spark-line"), state).toHaveLength(drawn ? 1 : 0);
      expect(container.querySelector("svg")?.getAttribute("class")?.includes("is-stale"), state).toBe(state === "stale");
      if (!drawn) expect(screen.getByRole("img").getAttribute("aria-label"), state).toMatch(/^Temperature: (No data|Unknown|Unavailable)$/);
    }
    expect(markup(<Sparkline values={[1, 2, 3]} state={state} />), state).not.toMatch(/\sstyle=/);
    cleanup();
  }
  const down = renderUi(<Sparkline values={[1, 2]} band={{ low: 1, high: 2 }} />, { apiDown: true });
  expect(down.container.querySelector("svg")?.getAttribute("class")).toContain("is-neutral");
  // All values missing is nothing drawn, never a flat line at 0.
  cleanup();
  expect(renderUi(<Sparkline values={[null, null]} />).container.querySelector(".spark-line, .spark-point, .spark-dot")).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- Delta
test("Delta: an arrow and words in ink, never a colour; no change; no comparison when unknown", () => {
  const { container } = renderUi(<Delta value={1.34} unit="pts" vs="previous 30 d" />);
  expect(text(container)).toBe("▲ up 1.3 pts vs previous 30 d");
  expect(container.querySelector(".delta")?.getAttribute("data-direction")).toBe("up");
  expect(container.querySelector("[aria-hidden=true]")?.textContent).toBe("▲ ");
  cleanup();
  expect(text(renderUi(<Delta value={-2} unit="%" vs="prior 7 d" />).container)).toBe("▼ down 2% vs prior 7 d");
  cleanup();
  expect(text(renderUi(<Delta value={0.02} unit="pts" vs="yesterday" />).container)).toBe("No change vs yesterday");
  cleanup();
  expect(text(renderUi(<Delta value={null} unit="pts" vs="yesterday" />).container)).toBe("— no comparison with yesterday");
  expect(plain(deltaAmount(-12, "gal"))).toBe("12 gal");
  expect(deltaAmount(1, "pts")).toBe("1 pt");
  expect(markup(<Delta value={1} vs="x" />)).not.toMatch(/\sstyle=|tag-ok|fault/);
});
