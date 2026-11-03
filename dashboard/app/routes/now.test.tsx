// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// SCR-01 Now (#1129) in the shell against #1122's fixture API in process, at 10:42 America/Denver: the answer with its
// values, one API-02 request serving the shell and Now, the degraded states, Stop all water with and without the
// irrigation firmware (and with API-IRR-01 not served, as at R1), the writes flag and roles, Acknowledge, and no
// greenhouse data in the server render.
import { renderToString } from "react-dom/server";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, expect, test, vi } from "vitest";
import { AppShell } from "../components/app-shell";
import { text } from "../components/test-render";
import { advance, sentAt, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import Now from "./now";

afterEach(() => { cleanup(); stopFixtureApi(); vi.unstubAllGlobals(); });

const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
function stub() {
  return createRoutesStub([{
    path: "/", Component: () => <AppShell build={BUILD} authError={null} />,
    children: [{ index: true, Component: Now }, { path: "activity", Component: () => <p>Activity</p> }, { path: "equipment", Component: () => <p>Equipment</p> }],
  }]);
}
async function open(fixture: FixtureOptions["fixture"], options: Omit<FixtureOptions, "fixture"> = {}, path = "/") {
  const api = startFixtureApi({ fixture, ...options });
  const Stub = stub();
  render(<Stub initialEntries={[path]} />);
  await advance(0);
  return api;
}
const plain = (value: string) => value.replaceAll(" ", " ");
const answer = () => plain(text(screen.getByRole("heading", { level: 1 })));
const banners = () => [...document.querySelectorAll("[data-banner]")].map((node) => plain(text(node)));
const chips = () => [...document.querySelectorAll(".now-running [data-chip]")].map((node) => plain(text(node)));
const running = () => plain(text(document.querySelector(".now-running")));
const toasts = (role: "status" | "alert") => [...screen.getByRole(role).querySelectorAll(".toast-body")]
  .map((body) => [...body.querySelectorAll("p")].map((line) => plain(text(line))).join(" "));
const stopButton = () => screen.queryByRole("button", { name: /^(Stop all water|Sending…)$/ });
const VERDICTS = /in band|above band|below band|band unavailable/;

test("in band: All OK with 64.8 °F, the band in force with its age, Updated 20 s ago, and the water card with Stop all water", async () => {
  await open("in-band");
  expect(answer()).toBe("All OK");
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("64.8 °F");
  const meta = [...document.querySelectorAll(".hero > div > .hero-meta")].map((node) => plain(text(node)));
  expect(meta).toContain("band in force 62.0–70.0 °F · DERIVED · 4 min");
  expect(meta.at(-1)).toBe("Updated 20 s ago");
  expect(banners()).toEqual([]);
  expect(running()).toContain("Nothing running");
  expect(text(document.querySelector("[data-chip=occupancy]"))).toBe("Nobody seen: misters may run (last person 05:10)");
  expect(plain(text(document.querySelector("[data-water=minutes]")))).toBe("Center 14 min · Wall 0 · South 0 · West 0");
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  expect(text(document.querySelector("#needs-someone .panel-empty"))).toBe("No open alerts");
});

test("one API-02 request serves the shell and Now; each read polls every 15 s", async () => {
  await open("in-band");
  expect(sentAt("API-02")).toEqual([0]);
  expect(sentAt("API-IRR-01")).toEqual([0]);
  expect(sentAt("API-14")).toEqual([0]);
  await advance(15_000);
  expect(sentAt("API-02")).toEqual([0, 15_000]);
  expect(sentAt("API-IRR-01")).toEqual([0, 15_000]);
  expect(sentAt("API-14")).toEqual([0, 15_000]);
});

test("with the heartbeat 4 min old and with climate 6 min old: the answer names it, no verdict, and alerts may be missing", async () => {
  await open("ingestor-not-running");
  expect(answer()).toBe("Ingestor not running since 10:38");
  expect(banners()[0]).toMatch(/^Ingestor not running since 10:38\. The controller keeps running on its own\./);
  expect(text(document.querySelector("main"))).not.toMatch(VERDICTS);
  expect(running()).toContain("Equipment state unknown since 10:38");
  expect(text(document.querySelector("#needs-someone .panel-empty"))).toBe("In-app alerts may be missing: ingestor not running since 10:38");
  expect(text(document.body)).not.toContain("No open alerts");
  cleanup();
  stopFixtureApi();
  await open("controller-not-reporting");
  expect(answer()).toBe("Controller not reporting since 10:36");
  expect(text(document.querySelector("main"))).not.toMatch(VERDICTS);
  expect(text(document.querySelector("#needs-someone .panel-empty"))).toBe("In-app alerts may be missing: no data since 10:36");
  expect(text(document.body)).not.toMatch(/controller offline|No open alerts/i);
});

test("API-02 failing: the answer says so at once, the banner after the second failure, and nothing reads All OK", async () => {
  await open("in-band", { fail: { "API-02": 503 } });
  expect(answer()).toBe("Can't reach the greenhouse data");
  expect(banners()).toEqual([]);
  // The second poll, after the 30 s backoff.
  await advance(30_000);
  expect(banners()).toEqual(["Dashboard API unavailable"]);
  expect(answer()).toBe("Can't reach the greenhouse data");
  expect(text(document.body)).not.toContain("All OK");
  expect(running()).toContain("Equipment state unknown");
  expect(document.querySelector(".tag-ok, .status-complete, .fresh-ok")).toBeNull();
});

test("the answer follows the newest API-02 read: a Stop's readback that answers clears it; a 401 ends the session, not row 1", async () => {
  const api = await open("l01-running", { fail: { "API-02": 503 } });
  expect(answer()).toBe("Can't reach the greenhouse data");
  // The API answers again. The shell's own next poll waits out its 30 s backoff; the Stop's readback reads API-02 at 5 s.
  api.respond({ method: "POST", url: "/__control", body: { fixture: "l01-running" } });
  fireEvent.click(stopButton()!);
  await advance(0);
  await advance(5000);
  await advance(1000);
  expect(sentAt("API-02").filter((at) => at > 0 && at < 30_000)).toEqual([5000]);
  expect(answer()).toBe("All OK");
  expect(running()).toContain("Nothing running");
  cleanup();
  stopFixtureApi();
  await open("in-band", { fail: { "API-02": 401 } });
  expect(screen.getByRole("alertdialog").textContent).toContain("Your session ended");
  expect(plain(text(document.querySelector(".hero-answer")))).toBe("Checking the greenhouse…");
});

test("Stop all water with the irrigation firmware: one API-IRR-07 request, the relay edge turns the wet chip off, and the toast offers Disable", async () => {
  const api = await open("l01-running");
  expect(chips()).toEqual(["Wall drip · run now · since 10:30 · 3 min left", "Nobody seen: misters may run (last person 05:10)"]);
  fireEvent.click(stopButton()!);
  expect(text(stopButton())).toBe("Sending…");
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: {} })]);
  // After the 202, API-02 and API-IRR-01 are read every 5 s until the relays read off.
  await advance(5000);
  // The readback's answer reaches the page on the next tick (TanStack batches its notifications on a timer).
  await advance(1000);
  expect(sentAt("API-02")).toContain(5000);
  expect(sentAt("API-IRR-01").some((at) => at > 0 && at <= 5000)).toBe(true);
  expect(chips()).toEqual(["Nobody seen: misters may run (last person 05:10)"]);
  expect(running()).toContain("Nothing running");
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it."]);
  expect(screen.getByRole("button", { name: "Disable L01" })).toBeTruthy();
  // Back to 15 s once the Stop has its answer.
  const before = sentAt("API-IRR-01").length;
  await advance(10_000);
  expect(sentAt("API-IRR-01").length - before).toBeLessThanOrEqual(1);
  expect(writes(api)).toHaveLength(1);
});

test("Stop all water before the irrigation firmware: the same one request, and the toast names what can still start, with no Disable", async () => {
  const api = await open(["l01-running", "before-engine"]);
  expect(chips()[0]).toBe("Wall drip · run now · since 10:30");
  fireEvent.click(stopButton()!);
  await advance(6000);
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
  expect(chips()).toEqual(["Nobody seen: misters may run (last person 05:10)"]);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting and fog can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("a running burst's chip gains Clear for an operator: one {kind: clear}, and the chip goes once the readback reads off", async () => {
  const api = await open("push-button-burst");
  expect(chips()).toEqual(["Fans burst · since 10:40", "Nobody seen: misters may run (last person 05:10)"]);
  fireEvent.click(screen.getByRole("button", { name: "Clear bursts" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-12", body: { kind: "clear" } })]);
  await advance(5000);
  await advance(1);
  expect(chips()).toEqual(["Nobody seen: misters may run (last person 05:10)"]);
  expect(screen.queryByRole("button", { name: "Clear bursts" })).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("the burst's chip reads from the readback alone, and its one Clear covers every burst", async () => {
  const api = await open("in-band");
  api.respond({ method: "POST", url: "/api/v1/equipment/override", headers: {}, body: { kind: "fans", minutes: 15 } });
  api.respond({ method: "POST", url: "/api/v1/equipment/override", headers: {}, body: { kind: "vent_bypass", minutes: 10 } });
  expect(screen.queryByRole("button", { name: "Clear bursts" })).toBeNull();
  await advance(15_000);
  await advance(1);
  expect(chips()).toEqual(["Fans burst · 14 min left", "Vent bypass · 9 min left", "Nobody seen: misters may run (last person 05:10)"]);
  expect(screen.getAllByRole("button", { name: "Clear bursts" })).toHaveLength(1);
});

test("before the irrigation firmware a push-button burst shows its chip with no Clear; so it does while the engine is unknown; a viewer has none", async () => {
  await open(["push-button-burst", "before-engine"]);
  expect(chips()[0]).toBe("Fans burst · since 10:40");
  expect(screen.queryByRole("button", { name: "Clear bursts" })).toBeNull();
  cleanup();
  stopFixtureApi();
  await open("push-button-burst", { fail: { "API-IRR-01": 404 } });
  expect(chips()[0]).toBe("Fans burst · since 10:40");
  expect(screen.queryByRole("button", { name: "Clear bursts" })).toBeNull();
  cleanup();
  stopFixtureApi();
  await open(["push-button-burst", "viewer-session"]);
  expect(chips()[0]).toBe("Fans burst · since 10:40");
  expect(screen.queryByRole("button", { name: "Clear bursts" })).toBeNull();
});

test("a latched mister on the water card: Clear fault beside the line sends one API-IRR-13, and the line goes once the latch reads gone", async () => {
  const api = await open("mister-fault");
  expect(plain(text(document.querySelector("[data-water=fault]")))).toBe("Center: faulted, no misting until midnight (watchdog)");
  fireEvent.click(within(document.querySelector<HTMLElement>(".water-fault")!).getByRole("button", { name: "Clear fault on L04" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  await advance(5000);
  await advance(1);
  expect(document.querySelector("[data-water=fault]")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Clear fault/ })).toBeNull();
});

test("as at R1, before API-IRR-01 is served: Water status unavailable with Retry, Stop all water still sends, the engine is unknown so no Disable", async () => {
  const api = await open("l01-running", { fail: { "API-IRR-01": 404 } });
  expect(text(document.querySelector(".now-water"))).toContain("Water status unavailable.");
  expect(text(document.querySelector("[data-chip=occupancy]"))).toBe("Occupancy unknown");
  expect(plain(text(document.querySelector('.hero > dl.stats > [data-tile="water"]')))).toContain("unavailable");
  fireEvent.click(stopButton()!);
  await advance(5000);
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("with changes switched off Stop stays live and Acknowledge is disabled; a viewer sees neither", async () => {
  await open(["alerts-merged", "writes-off"]);
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  expect(within(document.querySelector<HTMLElement>("#needs-someone")!).getAllByRole("button", { name: /^Acknowledge/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  cleanup();
  stopFixtureApi();
  await open(["alerts-merged", "viewer-session"]);
  expect(text(document.querySelector(".role-pill"))).toBe("Viewer");
  expect(stopButton()).toBeNull();
  expect(screen.queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
});

test("a missed SUNSET turns the Iris tile amber; Acknowledge sends one API-15 request and the answer follows the one count", async () => {
  await open("missed-sunset");
  expect(document.querySelector('.hero > dl.stats > [data-tile="iris"]')?.className).toContain("stat-warn");
  cleanup();
  stopFixtureApi();
  const api = await open("alerts-merged");
  expect(answer()).toBe("2 things need someone");
  expect(text(within(document.querySelector<HTMLElement>("#needs-someone")!).getByRole("heading", { level: 2 }))).toBe("Needs someone · 2");
  fireEvent.click(screen.getAllByRole("button", { name: /^Acknowledge/ })[0]);
  await advance(0);
  await advance(0);
  expect(writes(api).map((row) => [row.operationId, row.path])).toEqual([["API-15", "/api/v1/alerts/101/ack"]]);
  expect(answer()).toBe("1 thing needs someone");
  expect(text(document.querySelector("#needs-someone"))).toContain("Acknowledged by jason · 10:42");
});

test("the answer links to what explains it; on a phone #data opens All readings at the Data tile", async () => {
  await open("flow-valves-closed");
  expect(answer()).toBe("Water flowing with every Verdify valve closed");
  expect(screen.getByRole("link", { name: "Water flowing with every Verdify valve closed" }).getAttribute("href")).toBe("/activity?alert=201");
  expect(plain(text(document.querySelector("[data-water=flow]")))).toBe("Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min");
  cleanup();
  stopFixtureApi();
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(max-width: 860px)", media: query, addEventListener() {}, removeEventListener() {} }));
  await open("ingestor-not-running", {}, "/#data");
  await act(async () => {});
  expect(document.querySelector<HTMLDetailsElement>(".hero details")?.open).toBe(true);
});

test("the server render carries no greenhouse data: Now's loading state only, and no request", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const Stub = stub();
  const html = renderToString(<Stub initialEntries={["/"]} />);
  expect(html).toContain("Checking the greenhouse…");
  expect(html).not.toMatch(/All OK|64\.8|Nothing running|No open alerts|Stop all water|Nobody seen/);
  expect(fetch).not.toHaveBeenCalled();
});
