// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Control's own parts (redesign §4.4, §5.10, §5.11): the one pill's priority with CONFIRMED's 10 minutes, the text marks,
// the bounds bar in each state, and the plan's validity bar. Times are America/Denver with the clock at 10:42 on 2026-11-02.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Setting } from "../../lib/operator-api";
import { NBSP, denver, renderUi, text } from "../test-render";
import { BoundsBar, boundsWords } from "./bounds-bar";
import { FindSetting, SettingsSearch } from "./find-setting";
import { CONFIRMED_FOR_MS, Mark, isManual, settingPill, useRecentWrite, writtenAt } from "./marks";
import { ValidityBar } from "./validity-bar";

const NOW = Date.parse(denver("10:42"));
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

type Shape = Parameters<typeof settingPill>[0];
const base: Shape = {
  state: "confirmed", group: "planner_policy", readback_via: "cfg", readback: { value: 1.6, observed_at: denver("10:40"), reason: null }, applied_from: "plan",
  desired: null, plan: { value: 1.6, plan_id: "iris-20261102-0702", source: "iris", written_at: denver("07:02"), next: null },
};
const desired = (fields: Partial<NonNullable<Setting["desired"]>> = {}): NonNullable<Setting["desired"]> => ({
  id: 9, param: "mister_engage_kpa", value: 1.55, snapped_from: null, manual: true, manual_before: false, state: "confirmed", state_reason: null,
  confirmed_at: denver("10:40"), actor: { source: "dashboard", name: "jason" }, reason: null, created_at: denver("10:39"), ...fields,
});

test("one pill by priority: NOT CONFIRMED, REQUESTED, HELD, STALE, MANUAL, then CONFIRMED only while recent; none when steady", () => {
  const manual = { ...base, applied_from: "manual", desired: desired() } as Shape;
  expect(settingPill({ ...manual, state: "not_confirmed" }, { readbackStale: true, recent: true })).toBe("not_confirmed");
  expect(settingPill({ ...manual, state: "requested" }, { readbackStale: true })).toBe("requested");
  expect(settingPill({ ...manual, state: "held" }, { readbackStale: true })).toBe("held");
  expect(settingPill(manual, { readbackStale: true, recent: true })).toBe("stale");
  expect(settingPill(manual, { recent: true })).toBe("manual");
  const asked = { ...base, applied_from: "desired", desired: desired({ manual: false }) } as Shape;
  expect(settingPill(asked, { recent: true })).toBe("confirmed");
  expect(settingPill(asked, { recent: false })).toBeNull();
  expect(settingPill(base)).toBeNull();
  // STALE needs a device value to mute: never on a Band in force row, a row with no readback path, or no value.
  expect(settingPill({ ...base, group: "band_in_force" }, { readbackStale: true })).toBeNull();
  expect(settingPill({ ...base, readback_via: "none" }, { readbackStale: true })).toBeNull();
  expect(settingPill({ ...base, readback: { value: null, observed_at: null, reason: "not reported" } }, { readbackStale: true })).toBeNull();
  // A release is not manual; the written time is the value in force's own.
  expect(isManual({ desired: desired({ value: null }) })).toBe(false);
  expect([writtenAt(base), writtenAt(asked), writtenAt({ ...base, applied_from: "band" })]).toEqual([denver("07:02"), denver("10:39"), null]);
});

test("CONFIRMED's window is 10 minutes from the write, on the server's clock, and the hook re-renders when it closes", () => {
  expect(CONFIRMED_FOR_MS).toBe(600_000);
  function Probe({ at }: { at: string | null }) { return <output>{String(useRecentWrite(at))}</output>; }
  const { container } = renderUi(<><Probe at={denver("10:33")} /><Probe at={null} /><Probe at="not a time" /></>);
  expect([...container.querySelectorAll("output")].map((node) => node.textContent)).toEqual(["true", "false", "false"]);
  act(() => { vi.advanceTimersByTime(60_000); });
  expect(container.querySelector("output")!.textContent).toBe("false");
});

test("text marks are muted mono words with their data-badge hooks, never pills", () => {
  const { container } = renderUi(<p><Mark kind="ai" /> <Mark kind="derived" /> <Mark kind="no_readback" /></p>);
  expect([...container.querySelectorAll("[data-badge]")].map((node) => [node.getAttribute("data-badge"), node.className, text(node)])).toEqual([
    ["ai", "ctl-mark", "AI"], ["derived", "ctl-mark", "Derived"], ["no_readback", "ctl-mark", "No readback"],
  ]);
});

test("the bounds bar: the value as a dot, the plan and a request as rings, an arrowhead past an end, nothing coloured with the API down", () => {
  const bar = (props: Partial<Parameters<typeof BoundsBar>[0]>, session = {}) => {
    cleanup();
    return renderUi(<BoundsBar min={0.5} max={2.5} step={0.05} unit="kPa" value={1.6} {...props} />, session).container.querySelector(".ctl-bounds")!;
  };
  let node = bar({ planned: 1.7, requested: 1.55 });
  expect(node.querySelector(".ctl-bounds-dot")!.getAttribute("cx")).toBe("55%");
  expect(node.querySelector(".ctl-bounds-planned")!.getAttribute("cx")).toBe("60%");
  expect(node.querySelector(".ctl-bounds-requested")!.getAttribute("cx")).toBe("52.5%");
  expect(text(node.querySelector(".setting-bounds"))).toBe("Allowed 0.50–2.50 kPa · step 0.05");
  expect(node.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
  expect(node.querySelector("[style]")).toBeNull();
  // Past an end: no dot, an arrowhead at that end.
  node = bar({ value: 2.9 });
  expect(node.querySelector(".ctl-bounds-dot")).toBeNull();
  expect(node.querySelector(".ctl-bounds-clamp")!.getAttribute("d")).toBe("M1 7 L-6 2 L-6 12 Z");
  // Unknown: no dot and no zero.
  node = bar({ value: null });
  expect([node.classList.contains("is-unknown"), node.querySelector(".ctl-bounds-dot")]).toEqual([true, null]);
  expect(bar({ stale: true }).classList.contains("is-stale")).toBe(true);
  expect(bar({}, { apiDown: true }).classList.contains("is-neutral")).toBe(true);
  // No bounds to draw: the space is kept, empty.
  node = bar({ min: null });
  expect([node.className, node.children.length]).toEqual(["ctl-bounds ctl-bounds-none", 0]);
  expect(boundsWords({ min: 100, max: 600, step: 10, unit: "gal" })?.replaceAll(NBSP, " ")).toBe("Allowed 100–600 gal · step 10");
  expect(boundsWords({ min: 0, max: null })).toBeNull();
});

test("the validity bar fills from written to now, marks now, and stays bare for a time it cannot read", () => {
  const draw = (from: string, to: string) => { cleanup(); return renderUi(<ValidityBar from={from} to={to} now={NOW} />).container.querySelector("svg")!; };
  let svg = draw(denver("07:02"), denver("17:30"));
  expect([svg.querySelector(".ctl-validity-fill")!.getAttribute("width"), svg.querySelector(".ctl-validity-now")!.getAttribute("x1")]).toEqual(["35.03%", "35.03%"]);
  expect(svg.getAttribute("aria-hidden")).toBe("true");
  svg = draw(denver("07:02"), denver("09:00"));
  expect([svg.querySelector(".ctl-validity-fill")!.getAttribute("width"), svg.querySelector(".ctl-validity-now")]).toEqual(["100%", null]);
  svg = draw("soon", denver("17:30"));
  expect([svg.classList.contains("is-unknown"), svg.querySelector(".ctl-validity-fill")]).toEqual([true, null]);
});

test("the Control tabs share one search: Settings' field and Find a setting's, 22rem from 861 px, Find an outline that submits the form (#1308 visual QA 28)", () => {
  const { container } = renderUi(<><SettingsSearch value="" onChange={() => {}} /><FindSetting /></>);
  const [settings, find] = [...container.querySelectorAll(".ctl-find")];
  expect(settings.querySelector("input")?.getAttribute("placeholder")).toBe("Search settings: name or key");
  expect(find.querySelector("input")?.getAttribute("placeholder")).toBe("Find a setting");
  expect(find.querySelector("button")?.getAttribute("type")).toBe("submit");
  const css = readFileSync(join(process.cwd(), "app/styles/control.css"), "utf8");
  // A definite width (the old min(24rem,100%) resolved against a column sized by its content and fell back to the input's own).
  expect(css).toContain("@media(min-width:861px){.ctl-find input{width:22rem}}");
  expect(css).not.toMatch(/\.ctl-search input\{width:min\(/);
  // The global filled submit (styles.css button[type=submit]) is undone for Find: a way to a page, never a primary action.
  expect(css).toContain(".ctl-find .btn{flex:none;background:#fff;border-color:var(--line-strong);color:var(--forest)}");
});
