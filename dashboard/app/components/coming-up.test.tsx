// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// "Running and next" on Now (redesign §5.1) against the fixture API in process at 10:42: Running now, the track (its six
// hours of API-05 read only once it nears the screen), the phone's "Next 12 h" list with `[data-coming]` rows, and
// "Later"; each read fails alone and says so, never "nothing planned"; API-07 and API-IRR-02 poll every 60 s.
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { nowAnswer } from "../lib/now-answer";
import { panelRead, useApiRead, useNowRead } from "../lib/queries";
import { advance, renderWithApi, sentAt, startFixtureApi, stopFixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import { NOTHING_NEXT, RUNS_BEFORE_ENGINE, RunningAndNext, TRACK_LABEL } from "./coming-up";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

function Harness() {
  const now = useNowRead();
  const irrigation = panelRead(useApiRead("API-IRR-01", {}, { intervalMs: 15_000 }));
  return <RunningAndNext now={now.data ?? null} answer={nowAnswer({ now: now.data ?? null, failed: false })} irrigation={irrigation} />;
}
/** An IntersectionObserver that reports every element on screen at once, as a desktop's first screen would. */
function stubObserver() {
  const observe = vi.fn();
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private readonly callback: (entries: { isIntersecting: boolean }[]) => void) {}
    observe = (element: Element) => { observe(element); queueMicrotask(() => this.callback([{ isIntersecting: true }])); };
    disconnect = vi.fn();
    unobserve = vi.fn();
  });
  return observe;
}
async function open(fixture: FixtureOptions["fixture"], options: Omit<FixtureOptions, "fixture"> = {}) {
  startFixtureApi({ fixture, ...options });
  renderWithApi(<Harness />);
  await advance(0);
  await advance(0);
}
const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const coming = (kind: string) => [...document.querySelectorAll(`#coming-up [data-coming=${kind}]`)].map((node) => plain(text(node)));
const card = () => document.querySelector<HTMLElement>("#coming-up")!;

test("in band: Running now, the next 12 h in time order with who planned each change, and the lines with no schedule under Later", async () => {
  await open("in-band");
  expect(within(card()).getByRole("heading", { level: 2 }).textContent).toBe("Running and next");
  expect(text(card().querySelector(".now-next-now"))).toBe("Running nowNothing running");
  expect(coming("run")).toEqual(["11:47Center mister · 4 × 45 s · sensor-fault backup · only if VPD sensing is degraded", "13:47Center mister · 4 × 45 s · sensor-fault backup · only if VPD sensing is degraded"]);
  expect(coming("plan")).toEqual(["13:00Misting starts at VPD 1.60 → 1.70 kPa AI", "16:30Mist pulse gap 45.0 → 55.0 s AI"]);
  expect(coming("iris")).toEqual(["12:41Iris: Midday check", "16:50Iris: Sunset plan"]);
  expect(coming("sun")).toEqual(["16:58Sunset"]);
  expect(coming("later")).toEqual(["Later No schedule: Wall drip, South mister, West mister"]);
  // The next run shows here alone: nowhere else on Now (redesign §5.1).
  expect(card().querySelectorAll("[data-coming=plan] [data-badge=ai]")).toHaveLength(2);
});

test("a forecast rule reads Forecast rule · system, never AI; a paused line under Later says who paused it and why", async () => {
  await open(["plan-next", "l01-paused"]);
  expect(coming("plan")[1]).toBe("13:00Wait before misting 60.0 → 90.0 s · Forecast rule · system");
  expect(card().querySelectorAll("[data-coming=plan]")[1].querySelector("[data-badge=ai]")).toBeNull();
  expect(coming("iris")).toEqual(["16:50Iris: Sunset plan"]);
  // Now is not in the shell here: the pause reads by username.
  expect(coming("later")[0]).toMatch(/^Later Wall drip paused until 14:00 by jason: rain · next Wed 07:35 · /);
});

test("the track reads API-05's last 6 h only once it nears the screen; until then it holds its place", async () => {
  await open("in-band");
  expect(sentAt("API-05")).toEqual([]);
  expect(text(card().querySelector(".now-track .tt-empty"))).toBe("Loading");
  cleanup();
  stopFixtureApi();
  const observe = stubObserver();
  await open("in-band");
  await act(async () => {});
  await advance(0);
  expect(observe).toHaveBeenCalledOnce();
  expect(sentAt("API-05")).toEqual([0]);
  const track = screen.getByRole("group", { name: TRACK_LABEL });
  expect([...track.querySelectorAll(".tt-label")].map(text)).toEqual(["Heat", "Vent, fans", "Fog, mist", "Drips", "Main light", "Grow light", "Iris plans", "Setting changes", "Sun"]);
  // The planner's and the lights' row names are Terms: a tap opens the definition (#1339).
  expect([...track.querySelectorAll(".tt-label button.term")].map(text)).toEqual(["Main light", "Grow light", "Iris plans", "Setting changes"]);
  // A marker is a button: a tap opens its detail in full, whatever room its label had (#1340).
  const marker = track.querySelector<HTMLElement>("[data-row=plan] .tt-marker-button")!;
  fireEvent.click(marker);
  const detail = screen.getByRole("dialog", { name: "Setting changes at 13:00" });
  expect(plain(text(detail.querySelector(".tt-marker-detail")))).toBe("13:00 Misting starts at VPD 1.60 → 1.70 kPa · AI");
  expect(track.querySelector(".tt-now")).not.toBeNull();
  expect(track.querySelectorAll(".tt-night")).not.toHaveLength(0);
  expect(plain(text(track.querySelector(".tt-row:nth-child(1) .tt-span")))).toBe("Heat 1 (electric) 04:42–07:10");
});

test("each read fails alone with Retry, never reading nothing planned; before the irrigation firmware the runs say when they come", async () => {
  await open("in-band", { fail: { "API-07": 503 } });
  expect(text(card())).toContain("Couldn't load the plan. Retry");
  expect(coming("run")).toHaveLength(2);
  expect(coming("sun")).toEqual(["16:58Sunset"]);
  expect(text(card())).not.toContain(NOTHING_NEXT);
  fireEvent.click(within(card()).getByRole("button", { name: "Retry" }));
  cleanup();
  stopFixtureApi();
  await open("before-engine");
  expect(coming("run")).toEqual([]);
  expect(coming("engine")).toEqual([RUNS_BEFORE_ENGINE]);
});

test("API-07 and API-IRR-02 poll every 60 s with Now's parameters", async () => {
  await open("in-band");
  expect(sentAt("API-07")).toEqual([0]);
  expect(sentAt("API-IRR-02")).toEqual([0]);
  await advance(60_000);
  expect(sentAt("API-07")).toEqual([0, 60_000]);
  expect(sentAt("API-IRR-02")).toEqual([0, 60_000]);
});
