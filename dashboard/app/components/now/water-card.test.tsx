// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Water today on Now (redesign §5.1; US-NOW-04, US-WAT-13, US-WAT-18) over the fixtures at 10:42: each zone's minutes
// against its cap as a meter with when it last got water, the daily mist & fog limit's gallons, the flow in words, the
// line before the irrigation firmware, "Water status unavailable." with Retry, and no Stop all water (it is a quick action).
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { parseIrrigationStatus, type IrrigationStatus } from "../../lib/operator-api";
import type { PanelRead } from "../../lib/queries";
import { renderUi, text } from "../test-render";
import { BEFORE_ENGINE_LINE, WaterCard, WATER_UNAVAILABLE, flowText, minutesWords, saf4Text, zoneWater } from "./water-card";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const statusOf = (names: string | string[]): IrrigationStatus => parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"])!;
const read = (data: IrrigationStatus | null, failed = false): PanelRead<IrrigationStatus> => ({ data, failed, pending: data === null && !failed, retry: vi.fn() });
const renderCard = (irrigation: PanelRead<IrrigationStatus>, role: "operator" | "viewer" = "operator") => renderUi(<WaterCard irrigation={irrigation} />, { role, writesEnabled: true });
const row = (name: string) => document.querySelector(`[data-water="${name}"]`);
const rowWords = (name: string) => ({ amount: plain(text(row(name)?.querySelector(".now-water-amount"))), last: plain(text(row(name)?.querySelector(".now-water-last"))) });

test("in band: the daily mist & fog limit first, then a meter per zone, its minutes against the cap and when it last got water, and the flow", () => {
  renderCard(read(statusOf("in-band")));
  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Water today");
  expect(["wall", "center", "south", "west"].map(rowWords)).toEqual([
    { amount: "0 / 60 min", last: "last Fri 08:31, 12 min" }, { amount: "14 / 120 min", last: "misted 06:58 (VPD)" },
    { amount: "0 / 45 min", last: "misted Fri 14:20 (VPD)" }, { amount: "0 / 45 min", last: "misted 9 days ago (VPD)" },
  ]);
  // The drip zone fills in --eq-water, the mister zones in --eq-mister.
  expect([...document.querySelectorAll(".now-water-zones .meter")].map((meter) => meter.className.split(" ")[1])).toEqual(["meter-water", "meter-water", "meter-mister", "meter-mister", "meter-mister"]);
  expect(rowWords("saf4")).toEqual({ amount: "21 / 360 gal", last: "all misters and fog" });
  // #1349: words and a tip, never a bare "SAF-4".
  expect(text(row("saf4")?.querySelector(".now-water-name"))).toBe("Daily mist & fog limit primary daily limit");
  // #1339 (Jason, 2026-11-06): mist & fog is the primary daily water limit, so it leads the card, above the zones.
  expect(document.querySelector(".now-water-zones > li")?.getAttribute("data-water")).toBe("saf4");
  expect(screen.getByRole("button", { name: "About Daily mist & fog limit" })).toBeTruthy();
  expect(document.body.textContent).not.toContain("SAF-4");
  expect(text(row("flow"))).toBe("No flow with every valve closed");
  expect(screen.getByRole("link", { name: /^Water today, by line/ }).getAttribute("href")).toBe("/water");
  // Stop all water is the first quick action, never here.
  expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
});

test("a running zone says so with its cause; a cap reached or passed says so in words", () => {
  renderCard(read(statusOf("l01-running")));
  expect(rowWords("wall")).toEqual({ amount: "12 / 60 min", last: "watering now (run now)" });
  expect(row("wall")?.querySelector(".now-water-running")).not.toBeNull();
  cleanup();
  const over = statusOf("in-band");
  renderCard(read({ ...over, today: { ...over.today, zones: { ...over.today.zones, center: 132 } } }));
  expect(rowWords("center").amount).toBe("132 / 120 min · +12 min over");
  expect(row("center")?.querySelector(".meter.is-capped")).not.toBeNull();
});

test("the zones from API-IRR-01: caps summed over the enabled lines that have one, misters by kind; a zone with no enabled line not set up", () => {
  const zones = zoneWater(statusOf("in-band"), NOW);
  expect(zones.map(({ zone, minutes, cap, domain, last }) => [zone, minutes, cap, domain, last.state])).toEqual([
    ["wall", 0, 60, "water", "watered"], ["center", 14, 120, "mister", "watered"], ["south", 0, 45, "mister", "watered"], ["west", 0, 45, "mister", "watered"],
  ]);
  const disabled = statusOf("in-band");
  const lines = disabled.lines.map((line) => (line.line_id === "L01" ? { ...line, enabled: false } : line));
  renderCard(read({ ...disabled, lines }));
  expect(text(row("wall"))).toBe("WallNot set up");
  expect(row("wall")?.querySelector(".meter")).toBeNull();
});

test("before the irrigation firmware the minutes still read, with where L01 runs from; line states not reported mute the meters", () => {
  renderCard(read(statusOf(["l01-running", "before-engine"])));
  expect(text(row("before-engine"))).toBe(BEFORE_ENGINE_LINE);
  cleanup();
  renderCard(read(statusOf("controller-not-reporting")));
  expect(text(row("unknown"))).toBe("Line states not reported: the minutes are the last the controller sent.");
  expect(document.querySelectorAll(".now-water-zones .meter.is-stale")).toHaveLength(5);
});

test("the flow in words: flowing with every valve closed, stopped, unknown, and none", () => {
  renderCard(read(statusOf("flow-valves-closed")));
  expect(plain(text(row("flow")))).toBe("Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min");
  expect(row("flow")?.className).toBe("now-water-flow fault");
  cleanup();
  renderCard(read(statusOf("flow-stopped")));
  expect(text(row("flow"))).toBe("Flow stopped at 10:40");
  const leak = statusOf("in-band").leak;
  expect(flowText({ ...leak, active: null }).text).toBe("Flow: unknown");
  expect(flowText({ ...leak, active: true }).flowing).toBe(true);
  expect(saf4Text({ used: null, max: 360 })).toBe("— of 360 gal");
  expect(saf4Text({ used: 21, max: 360 })).toBe("21 of 360 gal");
});

test("minutes against a cap read whole minutes as the caps are set, never 2 h; reached and over in words; unknown never 0", () => {
  const said = [minutesWords(14, 120), minutesWords(60, 60), minutesWords(131.6, 120), minutesWords(null, 60), minutesWords(6, null), minutesWords(null, 0)];
  expect(said.map(({ amount, over }) => ({ amount: plain(amount), over: over && plain(over) }))).toEqual([
    { amount: "14 / 120 min", over: null }, { amount: "60 / 60 min", over: "cap reached" }, { amount: "132 / 120 min", over: "+12 min over" },
    { amount: "— / 60 min", over: null }, { amount: "6 min", over: null }, { amount: "—", over: null },
  ]);
});

test("API-IRR-01 failed or not served: Water status unavailable. with Retry, never idle; a viewer reads the same card", () => {
  const irrigation = read(null, true);
  renderCard(irrigation);
  const card = document.querySelector<HTMLElement>(".now-water")!;
  expect(text(card.querySelector(".panel-error p"))).toBe(WATER_UNAVAILABLE);
  expect(text(card)).not.toMatch(/Idle|No flow/);
  fireEvent.click(within(card).getByRole("button", { name: "Retry" }));
  expect(irrigation.retry).toHaveBeenCalledOnce();
  cleanup();
  renderCard(read(null));
  expect(document.querySelector(".now-water .skeleton")).not.toBeNull();
  cleanup();
  renderCard(read(statusOf("in-band")), "viewer");
  expect(rowWords("center").amount).toBe("14 / 120 min");
  expect(document.querySelector("[data-write-control]")).toBeNull();
});

test("#1348: a zone's minutes stay whole against its cap, with the VPD misting in them said apart, and the day's mist and fog on its own line", () => {
  const base = statusOf("in-band");
  const status: IrrigationStatus = { ...base, today: { ...base.today, climate_zones: { wall: 0, center: 14, south: 0, west: 0, fog: 6 }, climate: { minutes: 20, pulses: 23, gal: 7 } } };
  renderCard(read(status));
  expect(rowWords("center").amount).toBe("14 / 120 min · of which VPD 14 min");
  expect(rowWords("wall").amount).toBe("0 / 60 min");
  expect(plain(text(row("climate")))).toBe("Mist & fog today: 20 min · 7 gal · 23 pulses");
  cleanup();
  // A server before #1348 sends neither: no part and no climate line, never a 0 it does not know.
  renderCard(read({ ...base, today: { date: base.today.date, zones: base.today.zones } as IrrigationStatus["today"] }));
  expect(rowWords("center").amount).toBe("14 / 120 min");
  expect(row("climate")).toBeNull();
});
