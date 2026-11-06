// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Now (redesign §5.1, SCR-01) in the shell against #1122's fixture API in process, at 10:42 America/Denver: the answer
// with its values and band bars, one API-02 request serving the shell and Now, the degraded states, the quick actions
// (Stop all water with and without the irrigation firmware and with API-IRR-01 not served, the bursts), Needs someone,
// Water today, Running and next and the zones in their order, each fact once, the writes flag and roles, and no
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
const plain = (value: string) => value.replaceAll(String.fromCharCode(0xa0), " ");
const answer = () => plain(text(screen.getByRole("heading", { level: 1 })));
const banners = () => [...document.querySelectorAll("[data-banner]")].map((node) => plain(text(node)));
const chips = () => [...document.querySelectorAll(".now-running [data-chip]")].map((node) => plain(text(node)));
const running = () => plain(text(document.querySelector(".now-next-now")));
const main = () => plain(text(document.querySelector("main")));
const toasts = (role: "status" | "alert") => [...screen.getByRole(role).querySelectorAll(".toast-body")]
  .map((body) => [...body.querySelectorAll("p")].map((line) => plain(text(line))).join(" "));
const stopButton = () => screen.queryByRole("button", { name: /^(Stop all water|Sending…)$/ });
const VERDICTS = /in band|above band|below band|band unavailable/;
// Lets the panels' reads answer, then the zone tiles they then draw read their band.
const settled = async () => { await advance(0); await advance(0); };

test("in band: All OK with 64.8 °F, the band bars, Updated 20 s ago, Stop all water first, Water today and No open alerts", async () => {
  await open("in-band");
  expect(answer()).toBe("All OK");
  expect(plain(text(document.querySelector(".hero-temp")))).toBe("64.8 °F");
  expect(plain(text(document.querySelector("#data")))).toBe("Updated 20 s ago");
  expect([...document.querySelectorAll(".now-band")].map((row) => row.getAttribute("data-tile"))).toEqual(["temp", "vpd", "rh"]);
  expect(banners()).toEqual([]);
  expect(running()).toBe("Running nowNothing running");
  expect(text(document.querySelector("[data-chip=occupancy]"))).toBe("Nobody seen: misters may run (last person 05:10)");
  expect(plain(text(document.querySelector("[data-water=center] .now-water-amount")))).toBe("14 / 120 min");
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  expect(stopButton()?.closest(".now-actions")).not.toBeNull();
  // Nothing was resolved in the last 24 h either, so there is no link to an empty page.
  expect(text(document.querySelector("#needs-someone .panel-empty p"))).toBe("No open alerts · nothing resolved in 24 h");
  expect(document.querySelector("#needs-someone .panel-empty a")).toBeNull();
});

test("one API-02 request serves the shell and Now; API-02, API-IRR-01 and API-14 poll every 15 s; the track's API-05 waits for the screen", async () => {
  await open("in-band");
  expect(sentAt("API-02")).toEqual([0]);
  expect(sentAt("API-IRR-01")).toEqual([0]);
  expect(sentAt("API-14")).toEqual([0]);
  expect(sentAt("API-05")).toEqual([]);
  await advance(15_000);
  expect(sentAt("API-02")).toEqual([0, 15_000]);
  expect(sentAt("API-IRR-01")).toEqual([0, 15_000]);
  expect(sentAt("API-14")).toEqual([0, 15_000]);
});

test("each fact once: the open-alert count in the answer or Needs someone's head, one Stop all water, the next run only in Running and next", async () => {
  await open("alerts-merged");
  expect(answer()).toBe("2 things need someone");
  expect(text(within(document.querySelector<HTMLElement>("#needs-someone")!).getByRole("heading", { level: 2 }))).toBe("Needs someone");
  expect(screen.getAllByRole("button", { name: "Stop all water" })).toHaveLength(1);
  cleanup();
  stopFixtureApi();
  await open(["alerts-merged", "too-hot"]);
  expect(answer()).toBe("Out of band");
  expect(plain(text(document.querySelector(".now-condition [data-condition=temp]")))).toBe("Too hot: 84.1 °F (band 62–70 °F)");
  expect(text(within(document.querySelector<HTMLElement>("#needs-someone")!).getByRole("heading", { level: 2 }))).toBe("Needs someone · 2");
  cleanup();
  stopFixtureApi();
  await open("schedules-paused");
  await settled();
  // The week's next runs read under Later in Running and next, and nowhere else.
  const walker = document.createTreeWalker(document.querySelector("main")!, NodeFilter.SHOW_TEXT);
  const next: Element[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) if (/Wed 07:35/.test(node.textContent ?? "")) next.push(node.parentElement!);
  expect(next.length).toBeGreaterThan(0);
  expect(next.every((node) => node.closest("#coming-up") !== null)).toBe(true);
});

test("with the heartbeat 4 min old and with climate 6 min old: the answer names it, no verdict, and alerts may be missing", async () => {
  await open("ingestor-not-running");
  expect(answer()).toBe("Ingestor not running since 10:38");
  expect(banners()[0]).toMatch(/^Ingestor not running since 10:38\. The controller keeps running on its own\./);
  expect(main()).not.toMatch(VERDICTS);
  expect(running()).toContain("Equipment state unknown since 10:38");
  expect(text(document.querySelector("#needs-someone .panel-empty p"))).toBe("In-app alerts may be missing: ingestor not running since 10:38");
  expect(text(document.body)).not.toContain("No open alerts");
  cleanup();
  stopFixtureApi();
  await open("controller-not-reporting");
  expect(answer()).toBe("Controller not reporting since 10:36");
  expect(main()).not.toMatch(VERDICTS);
  expect(text(document.querySelector("#needs-someone .panel-empty p"))).toBe("In-app alerts may be missing: no data since 10:36");
  expect(text(document.body)).not.toMatch(/controller offline|No open alerts/i);
});

test("API-02 failing: the answer says so at once, the banner after the second failure, and then no bars, no In band today, nothing green", async () => {
  await open(["in-band", "time-in-band"], { fail: { "API-02": 503 } });
  expect(answer()).toBe("Can't reach the greenhouse data");
  expect(banners()).toEqual([]);
  // The second poll, after the 30 s backoff.
  await advance(30_000);
  expect(banners()).toEqual(["Dashboard API unavailable"]);
  expect(answer()).toBe("Can't reach the greenhouse data");
  expect(text(document.body)).not.toMatch(/All OK|in band/i);
  expect(document.querySelector(".now-bands")).toBeNull();
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

test("Stop all water with the irrigation firmware: one API-IRR-07 request, the relay edge takes the wet row off, and the toast offers Disable", async () => {
  const api = await open("l01-running");
  expect(chips()).toEqual(["Wall drip · run now · since 10:30 · 3 min left"]);
  fireEvent.click(stopButton()!);
  expect(text(stopButton())).toBe("Sending…");
  expect(document.querySelector("dialog")).toBeNull();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: {} })]);
  expect(text(document.querySelector(".now-stop"))).toContain("Waiting for the valves to read off");
  // After the 202, API-02 and API-IRR-01 are read every 5 s until the relays read off.
  await advance(5000);
  await advance(1000);
  expect(sentAt("API-02")).toContain(5000);
  expect(sentAt("API-IRR-01").some((at) => at > 0 && at <= 5000)).toBe(true);
  expect(chips()).toEqual([]);
  expect(running()).toContain("Nothing running");
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it."]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
  // Back to 15 s once the Stop has its answer.
  const before = sentAt("API-IRR-01").length;
  await advance(10_000);
  expect(sentAt("API-IRR-01").length - before).toBeLessThanOrEqual(1);
  expect(writes(api)).toHaveLength(1);
});

test("Stop all water before the irrigation firmware: the same one request, the toast names what can still start, with no Disable, and the bursts arrive later", async () => {
  const api = await open(["l01-running", "before-engine"]);
  expect(chips()[0]).toBe("Wall drip · run now · since 10:30");
  expect(text(document.querySelector("[data-arrives=bursts]"))).toBe("Bursts arrive with the irrigation firmware");
  expect(screen.queryByRole("button", { name: "Fans 15 min" })).toBeNull();
  fireEvent.click(stopButton()!);
  await advance(6000);
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
  expect(chips()).toEqual([]);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting and fog can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("Fans 15 min from the quick actions: one API-IRR-12 with no dialog, then the burst's row once the controller reads it back, with one Clear", async () => {
  const api = await open("in-band");
  fireEvent.click(screen.getByRole("button", { name: "Fans 15 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-12", body: { kind: "fans", minutes: 15 } })]);
  expect(document.querySelector("dialog")).toBeNull();
  await advance(5000);
  await advance(1);
  expect(chips()).toEqual(["Fans burst · 14 min left"]);
  expect(screen.getAllByRole("button", { name: "Clear bursts" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Clear bursts" }));
  await advance(0);
  expect(writes(api).at(-1)).toMatchObject({ operationId: "API-IRR-12", body: { kind: "clear" } });
  await advance(5000);
  await advance(1);
  expect(chips()).toEqual([]);
});

test("before the irrigation firmware a push-button burst shows its row with no Clear; so it does while the engine is unknown; a viewer has none", async () => {
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

test("a latched mister in Needs someone: Clear fault on its alert's row sends one API-IRR-13, and the button goes once the latch reads gone", async () => {
  const api = await open("mister-fault");
  const row = document.querySelector<HTMLElement>("#needs-someone li[data-severity]")!;
  expect(text(row.querySelector("h3"))).toBe("A mister zone faulted");
  fireEvent.click(within(row).getByRole("button", { name: "Clear fault on Center mister" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  await advance(5000);
  await advance(1);
  expect(screen.queryByRole("button", { name: /^Clear fault/ })).toBeNull();
});

test("as at R1, before API-IRR-01 is served: Water status unavailable with Retry, Stop all water still sends, the engine is unknown so no Disable", async () => {
  const api = await open("l01-running", { fail: { "API-IRR-01": 404 } });
  expect(text(document.querySelector(".now-water"))).toContain("Water status unavailable.");
  expect(text(document.querySelector("[data-chip=occupancy]"))).toBe("Occupancy unknown");
  fireEvent.click(stopButton()!);
  await advance(5000);
  expect(writes(api).map((row) => row.operationId)).toEqual(["API-IRR-07"]);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("with changes switched off Stop stays live while the bursts and Acknowledge are disabled; a viewer sees no quick actions and no Acknowledge", async () => {
  await open(["alerts-merged", "writes-off"]);
  expect(stopButton()?.hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "Fans 15 min" }).hasAttribute("disabled")).toBe(true);
  expect(within(document.querySelector<HTMLElement>("#needs-someone")!).getAllByRole("button", { name: /^Acknowledge/ }).every((button) => button.hasAttribute("disabled"))).toBe(true);
  cleanup();
  stopFixtureApi();
  await open(["alerts-merged", "viewer-session"]);
  expect(text(document.querySelector(".role-pill"))).toBe("Viewer");
  expect(stopButton()).toBeNull();
  expect(document.querySelector(".now-actions")).toBeNull();
  expect(screen.queryByRole("button", { name: /^Acknowledge/ })).toBeNull();
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
});

test("a missed SUNSET turns the Iris phrase amber; Acknowledge sends one API-15 request and the answer follows the one count", async () => {
  await open("missed-sunset");
  expect(document.querySelector(".now-status [data-tile=iris]")?.getAttribute("data-tone")).toBe("warn");
  cleanup();
  stopFixtureApi();
  const api = await open("alerts-merged");
  expect(answer()).toBe("2 things need someone");
  fireEvent.click(screen.getAllByRole("button", { name: /^Acknowledge/ })[0]);
  await advance(0);
  await advance(0);
  expect(writes(api).map((row) => [row.operationId, row.path])).toEqual([["API-15", "/api/v1/alerts/101/ack"]]);
  expect(answer()).toBe("1 thing needs someone");
  // In the shell the signed-in person reads by name, as on Activity.
  expect(text(document.querySelector("#needs-someone"))).toContain("Acknowledged by Jason · 10:42");
});

test("the answer is never a link: a quiet link under it goes to what explains it; the ingestor banner's #data opens All readings in place", async () => {
  await open("flow-valves-closed");
  expect(answer()).toBe("Water flowing with every Verdify valve closed");
  expect(screen.getByRole("heading", { level: 1 }).querySelector("a")).toBeNull();
  expect(document.querySelector(".now-condition a")?.getAttribute("href")).toBe("/activity?alert=201");
  expect(plain(text(document.querySelector("[data-water=flow]")))).toBe("Water flowing with every Verdify valve closed since 10:38 · 3 gal in 10 min");
  cleanup();
  stopFixtureApi();
  await open("ingestor-not-running", {}, "/#data");
  await act(async () => {});
  expect(document.querySelector<HTMLDetailsElement>("#readings")?.open).toBe(true);
});

test("time in band today against yesterday, from the daily keys", async () => {
  await open(["in-band", "time-in-band"]);
  await settled();
  expect(plain(text(document.querySelector(".now-tib")))).toBe("In band today temperature 96% · VPD 88% (yesterday 94% · 91%)");
});

test("the server render carries no greenhouse data: Now's loading state only, and no request", () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  const Stub = stub();
  const html = renderToString(<Stub initialEntries={["/"]} />);
  expect(html).toContain("Checking the greenhouse…");
  expect(html).not.toMatch(/All OK|64\.8|Nothing running|No open alerts|Stop all water|Nobody seen|In band today/);
  expect(html).not.toMatch(/\sstyle=/);
  expect(fetch).not.toHaveBeenCalled();
});

test("the order: hero, quick actions, Needs someone beside Water today, then Running and next, then the zones; Later reads the signed-in person by name", async () => {
  await open(["plan-next", "l01-paused"]);
  await settled();
  const ids = [".now-hero", ".now-actions", "#needs-someone", ".now-water", "#coming-up", "#zones"].map((selector) => document.querySelector(selector)!);
  for (let index = 1; index < ids.length; index++) expect(ids[index - 1].compareDocumentPosition(ids[index]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(document.querySelectorAll("#zones [data-zone]")).toHaveLength(5);
  // Now is in the shell, so the signed-in person reads by display name.
  expect(plain(text(document.querySelector("[data-coming=later]")))).toContain("Wall drip paused until 14:00 by Jason: rain · next Wed 07:35");
});

test("south's tile says in words that its probe is offline and it shows the West + East average, and opens its charts", async () => {
  await open("south-probe-out");
  await settled();
  const south = document.querySelector<HTMLElement>("#zones [data-zone=south]")!;
  expect(south.querySelector("[data-zone-tag]")?.getAttribute("data-zone-tag")).toBe("PEER");
  expect(south.querySelector("[data-zone-tag]")?.textContent).toBe("probe offline · West + East average");
  // No bare provenance code anywhere on Now.
  expect(main()).not.toMatch(/\bPEER\b/);
  expect(within(south).getByRole("link").getAttribute("href")).toBe("/graphs/zones?zone=south");
});

/** The codes Now never shows outside "All readings" (#1339): firmware interlocks, relay codes, planner triggers, provenance codes. */
const CODES = /SAF-\d|\bL(?:0[1-9]|1[01])\b|\b(?:SUNRISE|SUNSET|SOLAR_MAX|MIDNIGHT|TRANSITION|FORECAST_DEVIATION|HEARTBEAT|WEEKLY)\b|\b(?:PEER|HOUSE|DERIVED|MEASURED)\b/;
test("no code reads on Now outside All readings, in band, stale, running, a peer probe and lights that differ, track drawn (#1339)", async () => {
  for (const fixture of ["in-band", "stale-data", "l01-running", "south-peer", "grow-lights-differ", "forecast-rule"]) {
    vi.stubGlobal("IntersectionObserver", class {
      constructor(private readonly callback: (entries: { isIntersecting: boolean }[]) => void) {}
      observe = () => { queueMicrotask(() => this.callback([{ isIntersecting: true }])); };
      disconnect = vi.fn();
      unobserve = vi.fn();
    });
    await open(fixture);
    await act(async () => {});
    await settled();
    const page = document.querySelector("main")!.cloneNode(true) as Element;
    page.querySelector("details.now-readings")?.remove();
    expect(page.querySelector(".tt-rows"), fixture).not.toBeNull();
    expect(plain(page.textContent ?? ""), fixture).not.toMatch(CODES);
    // The words that replace them are there: the trigger names on the track and the list, the limit by name.
    if (fixture === "in-band") expect(plain(page.textContent ?? "")).toMatch(/Sunrise plan ✓.*Mist and fog water|Mist and fog water.*Sunrise plan ✓/s);
    // Every term the page defines opens its definition from the one glossary.
    expect(page.querySelectorAll("button.term").length, fixture).toBeGreaterThan(5);
    cleanup();
    stopFixtureApi();
    vi.unstubAllGlobals();
  }
});

test("a Term opens its definition in place and links to the whole glossary", async () => {
  await open("in-band");
  fireEvent.click(screen.getByRole("button", { name: "Mist and fog water" }));
  const dialog = screen.getByRole("dialog", { name: "Mist and fog water" });
  expect(text(dialog)).toContain("daily safety limit (360 gal)");
  expect(within(dialog).getByRole("link", { name: /All terms/ }).getAttribute("href")).toBe("/settings#glossary");
});
