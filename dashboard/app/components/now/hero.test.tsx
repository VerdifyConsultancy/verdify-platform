// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now's hero (#1129) over #1122's fixtures at 10:42: the answer as the page's one heading, linked to what explains it;
// 64.8 °F with VPD and RH beside it and their verdicts; the band sub-line with its age from `band.observed_at`, or none;
// "Updated 20 s ago"; no verdict and STALE values while rows 1 to 3 or 7 hold; a null reading as "—" with its reason.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { nowAnswer } from "../../lib/now-answer";
import { parseIrrigationStatus, parseNow, type Now } from "../../lib/operator-api";
import { renderUi, text } from "../test-render";
import { NowHero, bandLine } from "./hero";

const NOW = Date.parse("2026-11-02T17:42:00Z");
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const plain = (value: string) => value.replaceAll(" ", " ");
const nowOf = (names: string | string[]): Now => parseNow(fixture(names, { now: NOW })["API-02"])!;
function renderHero(names: string | string[], { failed = false, data = true, retry = vi.fn(), apiDown = false } = {}) {
  const now = data ? nowOf(names) : null;
  const irrigation = { data: parseIrrigationStatus(fixture(names, { now: NOW })["API-IRR-01"]), failed: false, pending: false, retry: vi.fn() };
  const view = renderUi(<NowHero now={now} answer={nowAnswer({ now, failed })} irrigation={irrigation} onRetry={retry} />, { apiDown });
  return { ...view, retry };
}
const heading = () => plain(text(screen.getByRole("heading", { level: 1 })));
const meta = () => [...document.querySelectorAll(".hero > div > .hero-meta")].map((node) => plain(text(node)));
const VERDICTS = /in band|above band|below band|band unavailable/;

test("in band: All OK with 64.8 °F, VPD and RH beside it, the band in force with its age, and Updated 20 s ago", () => {
  renderHero("in-band");
  expect(heading()).toBe("All OK");
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("64.8 °F");
  expect(meta()).toEqual([
    "in band",
    "VPD 0.62 kPa · in bandRH 71.0% · no RH band",
    "band in force 62.0–70.0 °F · DERIVED · 4 min",
    "Updated 20 s ago",
  ]);
  expect(plain(text(document.querySelector(".eyebrow")))).toBe("Greenhouse · Longmont · America/Denver");
  expect(document.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("ok");
});

test("the age ticks each second with no live region, and the band line states no age while band.observed_at is null", async () => {
  renderHero("in-band");
  await vi.advanceTimersByTimeAsync(5000);
  expect(meta().at(-1)).toBe("Updated 25 s ago");
  expect(document.querySelector(".hero [aria-live], .hero [role=status]")).toBeNull();
  cleanup();
  renderHero("band-age-unknown");
  expect(meta()).toContain("band in force 62.0–70.0 °F · DERIVED");
  const band = nowOf("in-band").band;
  expect(plain(bandLine({ ...band, temp_f: { low: null, high: 70, verdict: "unavailable" } }, NOW))).toBe("band unavailable");
});

test("the raw mode code, stage and reason sit in All readings, beside every source's time", () => {
  renderHero("in-band");
  const details = document.querySelector(".hero details")!;
  expect(text(details.querySelector("summary"))).toBe("All readings");
  const readings = plain(text(details.querySelector(".now-readings")));
  expect(readings).toContain("Mode codeIDLE · since 09:55");
  expect(readings).toContain("Mode reasonidle");
  expect(readings).toContain("Indoor temperature64.8 °F at 10:41");
  expect(readings).toContain("Ingestor heartbeat10:41 (20 s old)");
  expect(readings).toContain("Setpoints pipeline (writer)10:01 (41 min old)");
});

test("with the heartbeat 4 min old: Ingestor not running since 10:38, linked to the Data tile; no verdict, values STALE", () => {
  renderHero("ingestor-not-running");
  expect(heading()).toBe("Ingestor not running since 10:38");
  expect(screen.getByRole("link", { name: "Ingestor not running since 10:38" }).getAttribute("href")).toBe("/#data");
  expect(text(document.querySelector(".hero"))).not.toMatch(VERDICTS);
  expect(document.querySelector(".hero-temp")?.className).toContain("value-muted");
  expect(meta().at(-1)).toBe("Last data 4 min ago (10:38) Stale");
  expect(document.querySelector(".hero-meta .dot")?.getAttribute("data-fresh")).toBe("off");
});

test("with the heartbeat fresh and climate 6 min old: Controller not reporting since 10:36, linked to Equipment; no verdict", () => {
  renderHero("controller-not-reporting");
  expect(heading()).toBe("Controller not reporting since 10:36");
  expect(screen.getByRole("link", { name: "Controller not reporting since 10:36" }).getAttribute("href")).toBe("/equipment");
  expect(text(document.querySelector(".hero"))).not.toMatch(VERDICTS);
  expect(text(document.body)).not.toMatch(/controller offline/i);
});

test("stale data: Waiting for fresh data (last 14 min ago), STALE with the last data time, and no verdict", () => {
  renderHero("stale-data");
  expect(heading()).toBe("Waiting for fresh data (last 14 min ago)");
  expect(meta().at(-1)).toBe("Last data 14 min ago (10:28) Stale");
  expect(text(document.querySelector(".hero"))).not.toMatch(VERDICTS);
});

test("too hot links to Graphs; a null RH reads — with its reason, never 0", () => {
  renderHero("too-hot");
  expect(heading()).toBe("Too hot: 84.1 °F (target 62–70 °F)");
  expect(screen.getByRole("link", { name: /^Too hot/ }).getAttribute("href")).toBe("/graphs");
  expect(meta()[0]).toBe("above band");
  cleanup();
  renderHero("rh-missing");
  expect(meta()).toContain("VPD 0.62 kPa · in bandRH — (No indoor RH)");
  const rh = document.querySelector('.hero > dl.stats > [data-tile="rh"]')!;
  expect(plain(text(rh))).toBe("RH: open Graphs—No indoor RHno RH band");
  expect(plain(text(rh))).not.toMatch(/\b0(\.0)?%/);
});

test("the newest API-02 read failed: Can't reach the greenhouse data with Retry, values muted, nothing green and no verdict", () => {
  const { retry } = renderHero("in-band", { failed: true });
  expect(heading()).toBe("Can't reach the greenhouse data");
  expect(text(document.querySelector(".hero"))).not.toMatch(VERDICTS);
  expect(document.querySelector(".hero-temp")?.className).toContain("value-muted");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledTimes(1);
  cleanup();
  renderHero("in-band", { failed: true, data: false, apiDown: true });
  expect(heading()).toBe("Can't reach the greenhouse data");
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("—");
  expect(text(document.querySelector(".hero"))).toContain("No answer from the greenhouse data");
  expect(document.querySelector(".hero .tag-ok, .hero .status-complete, .hero .fresh-ok")).toBeNull();
});

test("before API-02 first answers: the hero keeps its height and claims nothing", () => {
  renderHero("in-band", { data: false });
  expect(heading()).toBe("Checking the greenhouse…");
  expect(document.querySelector(".hero")?.getAttribute("aria-busy")).toBe("true");
  expect(document.querySelectorAll(".hero .skeleton")).toHaveLength(2);
  expect(text(document.querySelector(".hero"))).not.toMatch(/All OK|64\.8/);
});
