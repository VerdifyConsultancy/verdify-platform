// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The shell against #1122's fixture API in process (fetch is stubbed onto createFixtureHandler): the polls, the banners
// on every page, the nav count, role gating and the changes-off state, the session-ended card and SCR-09's sign-in
// pages. Times are America/Denver with the fixture clock at 10:42.
import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { REVISION } from "../../e2e/fixtures/world.mjs";
import routes from "../routes";
import Activity from "../routes/activity";
import Equipment from "../routes/equipment";
import Graphs from "../routes/graphs";
import Scorecard from "../routes/graphs-scorecard";
import More from "../routes/more";
import Now from "../routes/now";
import Plan from "../routes/plan";
import Settings from "../routes/settings";
import WaterRoute from "../routes/water";
import WaterHistory from "../routes/water-history";
import WaterSchedules from "../routes/water-schedules";
import type { AuthError } from "../lib/page-context";
import { NOW_POLL_MS } from "../lib/shell";
import { AppShell } from "./app-shell";
import { Badge } from "./badge";
import { Button } from "./button";
import { FreshnessDot } from "./freshness";
import { text } from "./test-render";

const NOW = Date.parse("2026-11-02T17:42:00Z");
const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
/** Every screen route of app/routes.ts by path, with its component. */
const SCREENS = [
  ["/", Now], ["/graphs", Graphs], ["/graphs/scorecard", Scorecard], ["/plan", Plan], ["/water", WaterRoute], ["/water/schedules", WaterSchedules],
  ["/water/history", WaterHistory], ["/settings", Settings], ["/activity", Activity], ["/equipment", Equipment], ["/more", More],
] as const;
/** A page with one write control, Stop and a freshness dot and green badge, to see role gating and API down on. */
function Probe() {
  return <div>
    <Button write>Save</Button>
    <Button write variant="stop">Stop all water</Button>
    <FreshnessDot stale={false} />
    <Badge kind="confirmed" />
  </div>;
}

type Options = { fail?: Record<string, number> };
let requests: string[];
function serve(fixture: string | string[], { fail }: Options = {}) {
  const api = createFixtureHandler({ fixture, clock: () => NOW, ...(fail ? { fail } : {}) });
  requests = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init: RequestInit = {}) => {
    requests.push(input);
    const out = api.respond({ method: init.method, url: input, headers: init.headers as Record<string, string> | undefined, body: init.body as string | undefined });
    return new Response(out.body === null || out.body === undefined ? null : JSON.stringify(out.body), { status: out.status, headers: out.headers });
  }));
}
function mount(path: string, { authError = null, probe = false }: { authError?: AuthError | null; probe?: boolean } = {}) {
  const children = [
    ...SCREENS.map(([to, Component]) => (to === "/" ? { index: true, Component } : { path: to.slice(1), Component })),
    { path: "probe", Component: Probe },
    { path: "auth/*", Component: () => null },
  ];
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build={BUILD} authError={authError} />, children }]);
  return render(<StrictMode><Stub initialEntries={[probe ? "/probe" : path]} /></StrictMode>);
}
async function advance(milliseconds: number) { await act(async () => { await vi.advanceTimersByTimeAsync(milliseconds); }); }
const banners = () => [...document.querySelectorAll("[data-banner]")].map((node) => ({ tone: node.getAttribute("data-banner"), text: text(node) }));
const nav = (label: string) => screen.getByRole("navigation", { name: label });

beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(NOW));
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

test("the routes: SCR-01 to SCR-07, /more, SCR-09 through /auth/* and a catch-all; no /plants", () => {
  const paths = routes.map((route) => ("index" in route && route.index ? "/" : `/${route.path}`));
  expect(paths).toEqual(["/", "/graphs", "/graphs/scorecard", "/plan", "/water", "/water/schedules", "/water/history", "/settings", "/activity", "/equipment", "/more", "/auth/*", "/*"]);
  expect(paths).not.toContain("/plants");
});

test("in band: the shell reads API-01 and API-02 once each, shows no banner, and every route renders its head", async () => {
  serve("in-band");
  for (const [path] of SCREENS) {
    mount(path); await advance(0);
    expect(banners(), path).toEqual([]);
    expect(screen.getAllByRole("heading", { level: 1 }), path).toHaveLength(1);
    cleanup();
  }
  mount("/"); await advance(0);
  expect(requests.slice(-2).sort()).toEqual(["/api/v1/me", "/api/v1/now"]);
  // API-02 every 15 s, API-01 every 60 s: one poll each, never a second.
  requests = [];
  await advance(NOW_POLL_MS);
  expect(requests).toEqual(["/api/v1/now"]);
  await advance(60_000 - NOW_POLL_MS);
  expect(requests.filter((path) => path === "/api/v1/me")).toHaveLength(1);
});

test("a server render carries no greenhouse data: no banner, count, role, name or API build, and no request", () => {
  serve("alerts-merged");
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build={BUILD} authError={null} />, children: SCREENS.map(([to, Component]) => (to === "/" ? { index: true, Component } : { path: to.slice(1), Component })) }]);
  const html = renderToString(<Stub initialEntries={["/activity"]} />);
  expect(html).toContain('aria-label="Verdify dashboard, home"');
  expect(html).toContain("fedcba987654");
  expect(html).toContain('action="/auth/logout"');
  expect(html).not.toMatch(/data-banner|nav-count|role-pill|header-name|Jason|Operator|open alerts/);
  expect(html).not.toContain(REVISION.slice(0, 12));
  expect(requests).toEqual([]);
});

test("the header: lockup, sections with the count, role pill, name and Sign out; the footer names both builds", async () => {
  serve("alerts-merged");
  mount("/activity"); await advance(0);
  const brand = screen.getByRole("link", { name: "Verdify dashboard, home" });
  expect(brand.getAttribute("href")).toBe("/");
  expect(brand.querySelectorAll(".brand-leaf, .brand-mark, .brand-dot")).toHaveLength(3);
  expect(text(brand)).toBe("erdify · Dashboard");
  const sections = within(nav("Sections")).getAllByRole("link");
  expect(sections.map(text)).toEqual(["Now", "Graphs", "Plan", "Water", "Settings", "Activity 2 open alerts", "Equipment"]);
  expect(sections.filter((link) => link.getAttribute("aria-current") === "page").map(text)).toEqual(["Activity 2 open alerts"]);
  expect(text(document.querySelector(".role-pill"))).toBe("Operator");
  expect(text(document.querySelector(".header-name"))).toBe("Jason");
  const signOut = document.querySelector<HTMLFormElement>(".header-signout")!;
  expect([signOut.method, signOut.getAttribute("action"), text(signOut)]).toEqual(["post", "/auth/logout", "Sign out"]);
  const footer = document.querySelector("footer")!;
  expect(text(footer.querySelector(".build-line"))).toBe(`Verdify dashboard · build fedcba987654 · API ${REVISION.slice(0, 12)}`);
  expect(within(footer).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["https://graphs.verdify.ai/", "https://lab.verdify.ai/", "/fonts/OFL-sans.txt", "/fonts/OFL-mono.txt"]);
});

test("the phone bar holds Now, Water, Graphs, Activity with the count and More; More owns the sections it lists", async () => {
  serve("alerts-merged");
  mount("/plan"); await advance(0);
  const tabs = within(nav("Sections, phone")).getAllByRole("link");
  expect(tabs.map(text)).toEqual(["Now", "Water", "Graphs", "Activity 2 open alerts", "More"]);
  expect(tabs.map((tab) => tab.getAttribute("href"))).toEqual(["/", "/water", "/graphs", "/activity", "/more"]);
  expect(tabs.filter((tab) => tab.getAttribute("aria-current") === "page").map(text)).toEqual(["More"]);
  cleanup();
  mount("/more"); await advance(0);
  const main = screen.getByRole("main");
  expect(within(main).getAllByRole("link").map(text)).toEqual(["Plan ›", "Settings ›", "Equipment ›"]);
  expect(text(within(main).getByText(/Signed in as/))).toBe("Signed in as Jason");
  expect(within(main).getByRole("button", { name: "Sign out" }).closest("form")?.getAttribute("action")).toBe("/auth/logout");
  expect(text(main.querySelector(".build-line"))).toBe(`Verdify dashboard · build fedcba987654 · API ${REVISION.slice(0, 12)}`);
});

test("a heartbeat 4 min old reads Ingestor not running on every page", async () => {
  serve("ingestor-not-running");
  for (const [path] of SCREENS) {
    mount(path); await advance(0);
    expect(banners(), path).toEqual([{ tone: "fault", text: "Ingestor not running since 10:38. The controller keeps running on its own. Data on Now ›" }]);
    cleanup();
  }
});

test("a fresh heartbeat with climate 6 min old reads Controller not reporting and links to Equipment", async () => {
  serve("controller-not-reporting");
  mount("/water"); await advance(0);
  expect(banners()).toHaveLength(1);
  expect(banners()[0].text).toMatch(/^Controller not reporting since 10:36\. If it has power, it keeps running VPD misting/);
  expect(document.body.textContent).not.toMatch(/controller offline/i);
  expect(within(document.querySelector<HTMLElement>("[data-banner]")!).getByRole("link", { name: "Equipment" }).getAttribute("href")).toBe("/equipment");
});

test("API-02 failing twice in a row reads Dashboard API unavailable, and nothing is green", async () => {
  serve("api-down");
  mount("/probe", { probe: true }); await advance(0);
  // One failure is not enough.
  expect(banners()).toEqual([]);
  expect(document.querySelector("[data-fresh]")?.getAttribute("data-fresh")).toBe("ok");
  // The second poll comes after the backoff (30 s after one failure).
  await advance(2 * NOW_POLL_MS);
  expect(banners()).toEqual([{ tone: "fault", text: "Dashboard API unavailable" }]);
  expect(document.querySelector("[data-fresh]")?.getAttribute("data-fresh")).toBe("off");
  expect(document.querySelector("[data-badge=confirmed]")?.className).toBe("status");
});

test("water flowing with every valve closed, then Flow stopped at HH:MM, links to its alert", async () => {
  serve("flow-stopped");
  mount("/graphs"); await advance(0);
  expect(banners()).toEqual([{ tone: "fault", text: "Flow stopped at 10:40 Open the alert ›" }]);
  expect(screen.getByRole("link", { name: /Open the alert/ }).getAttribute("href")).toBe("/activity?alert=201");
});

test("stuck delivery and changes off are warn banners; with changes off an operator's controls are disabled but Stop", async () => {
  serve(["stuck-delivery", "writes-off"]);
  mount("/probe", { probe: true }); await advance(0);
  expect(banners()).toEqual([
    { tone: "warn", text: "Settings not reaching the controller since 06:10 (4 differ) Requested settings ›" },
    { tone: "warn", text: "Changes are switched off; reading still works" },
  ]);
  expect(screen.getByRole("link", { name: /Requested settings/ }).getAttribute("href")).toBe("/settings?filter=requested");
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Stop all water" }).hasAttribute("disabled")).toBe(false);
});

test("roles come from API-01: an operator gets write controls; a viewer session renders none on any route", async () => {
  serve("in-band");
  mount("/probe", { probe: true });
  // Until API-01 answers the page is a viewer's.
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(0);
  await advance(0);
  expect(document.querySelectorAll("[data-write-control]")).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(false);
  cleanup();
  serve("viewer-session");
  for (const path of [...SCREENS.map(([to]) => to), "/probe"]) {
    mount(path, { probe: path === "/probe" }); await advance(0);
    expect(text(document.querySelector(".role-pill")), path).toBe("Viewer");
    expect(document.querySelectorAll("[data-write-control]"), path).toHaveLength(0);
    cleanup();
  }
});

test("a 401 during polling shows the session-ended card, which signs in back to the same URL and query", async () => {
  serve("in-band", { fail: { "API-02": 401 } });
  mount("/activity?target=L01"); await advance(0);
  const card = screen.getByRole("alertdialog", { name: "Your session ended" });
  expect(text(card)).toBe("Your session endedSign in again.Sign in");
  const signIn = within(card).getByRole("link", { name: "Sign in" });
  expect(signIn.getAttribute("href")).toBe("/auth/login?returnTo=%2Factivity%3Ftarget%3DL01");
  expect(document.activeElement).toBe(signIn);
  expect(document.querySelector(".app-page")?.hasAttribute("inert")).toBe(true);
  // A 401 is not the API failing, and every poll stops.
  expect(banners()).toEqual([]);
  const sent = requests.length;
  await advance(10 * 60_000);
  expect(requests).toHaveLength(sent);
});

test.each([
  ["failed", 400, "Sign-in didn't complete", "Try again.", "Sign in"],
  ["unavailable", 503, "Sign-in is unavailable", "Try again in a minute.", "Sign in"],
  ["no_access", 403, "No dashboard access", "Your account doesn't have dashboard access. Ask Jason to add you to a dashboard group.", "Sign out"],
] as const)("a %s sign-in shows SCR-09's card with the header alone, and polls nothing", async (reason, _status, title, body, action) => {
  serve("in-band");
  mount("/auth/callback", { authError: { reason, requestId: "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f" } }); await advance(0);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(title);
  expect(screen.getByText(body)).toBeTruthy();
  const control = action === "Sign in" ? screen.getByRole("link", { name: "Sign in" }) : screen.getByRole("button", { name: "Sign out" });
  if (action === "Sign in") expect(control.getAttribute("href")).toBe("/auth/login");
  else expect(control.closest("form")?.getAttribute("action")).toBe("/auth/logout");
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(document.querySelector(".role-pill, .header-signout, [data-banner]")).toBeNull();
  expect(text(document.querySelector(".build-line"))).toBe("Verdify dashboard · build fedcba987654 · API —");
  expect(screen.getByText(/Request id/).textContent).toBe("Request id 4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f");
  expect(requests).toEqual([]);
});
