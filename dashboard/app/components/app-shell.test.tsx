// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The shell against #1122's fixture API in process (fetch is stubbed onto createFixtureHandler): the polls, the banners
// on every page, the nav count, role gating and the changes-off state, the session-ended card and SCR-09's sign-in
// pages. Times are America/Denver with the fixture clock at 10:42.
import { StrictMode } from "react";
import { renderToString } from "react-dom/server";
import { useMutation } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLoaderData, useLocation } from "react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { createFixtureHandler } from "../../e2e/fixture-api.mjs";
import { REVISION } from "../../e2e/fixtures/world.mjs";
import { FakeResizeObserver, MockPlot } from "../charts/fixtures/uplot-mock";
import routes from "../routes";
import Activity from "../routes/activity";
import CropsRoute from "../routes/crops";
import CropsHistory from "../routes/crops-history";
import CropsMap from "../routes/crops-map";
import ActivityLog from "../routes/activity-log";
import Equipment from "../routes/equipment";
import Graphs from "../routes/graphs";
import Statistics from "../routes/graphs-statistics";
import GraphZones from "../routes/graphs.zones";
import GraphsLighting from "../routes/graphs.lighting";
import Now from "../routes/now";
import Plan from "../routes/plan";
import PlannerExperiments from "../routes/planner-experiments";
import PlannerHistory from "../routes/planner-history";
import Settings from "../routes/settings";
import Band from "../routes/band";
import WaterRoute from "../routes/water";
import WaterBoard from "../routes/water-board";
import WaterHistory from "../routes/water-history";
import WaterSchedules from "../routes/water-schedules";
import { apiWrite } from "../lib/operator-api";
import type { AuthError } from "../lib/page-context";
import { NOW_POLL_MS } from "../lib/shell";
import { AppShell } from "./app-shell";
import { Badge } from "./badge";
import { Button } from "./button";
import { FreshnessDot } from "./freshness";
import { text } from "./test-render";

// Graphs (#1130) and Water history (#1127's bars) draw charts: uPlot and ResizeObserver are stand-ins here, as in the chart tests.
vi.mock("uplot", async () => ({ default: (await import("../charts/fixtures/uplot-mock")).MockPlot }));

const NOW = Date.parse("2026-11-02T17:42:00Z");
const BUILD = "fedcba9876543210fedcba9876543210fedcba98";
/** Every screen route of app/routes.ts by path, with its component; Water's three views render inside its layout. */
const SCREENS = [
  ["/", Now], ["/water", WaterBoard], ["/water/schedules", WaterSchedules], ["/water/history", WaterHistory], ["/crops", CropsMap], ["/crops/history", CropsHistory],
  ["/graphs", Graphs], ["/graphs/zones", GraphZones],
  ["/graphs/lighting", GraphsLighting], ["/graphs/statistics", Statistics], ["/activity", Activity], ["/activity/log", ActivityLog], ["/settings", Settings], ["/band", Band], ["/plan", Plan], ["/equipment", Equipment],
  ["/planner/history", PlannerHistory], ["/planner/experiments", PlannerExperiments],
] as const;
const WATER_VIEWS: readonly string[] = ["/water", "/water/schedules", "/water/history"];
const CROPS_VIEWS: readonly string[] = ["/crops", "/crops/history"];
/** The screens as the route stub nests them, as app/routes.ts does: the Water views under `routes/water.tsx`. */
function screenRoutes() {
  return [
    ...SCREENS.filter(([to]) => !WATER_VIEWS.includes(to) && !CROPS_VIEWS.includes(to)).map(([to, Component]) => (to === "/" ? { index: true, Component } : { path: to.slice(1), Component })),
    { path: "water", Component: WaterRoute, children: [{ index: true, Component: WaterBoard }, { path: "schedules", Component: WaterSchedules }, { path: "history", Component: WaterHistory }] },
    { path: "crops", Component: CropsRoute, children: [{ index: true, Component: CropsMap }, { path: "history", Component: CropsHistory }] },
  ];
}
/** A page with one write control, Stop and a freshness dot and green badge, to see role gating and API down on. */
function Probe() {
  return <div>
    <Button write>Save</Button>
    <Button write variant="stop">Stop all water</Button>
    <FreshnessDot stale={false} />
    <Badge kind="confirmed" />
  </div>;
}

/** One write through the client, as #1125's hooks send them, with one Idempotency-Key. */
function WriteProbe() {
  const save = useMutation({
    mutationFn: () => apiWrite("API-10", { param: "mister_engage_kpa" }, { value: 1.53, manual: true, reason: "Vandas drying" }, "4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f"),
  });
  return <button type="button" onClick={() => save.mutate()}>Send the write</button>;
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
    ...screenRoutes(),
    { path: "probe", Component: Probe },
    { path: "write", Component: WriteProbe },
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
  MockPlot.reset();
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

test("the routes: the seven sections' views, the scorecard's and Planner's redirects, SCR-09 through /auth/* and a catch-all; no /more or /plants", () => {
  // A layout's own path is its index child's: Water's three views nest under routes/water.tsx.
  type Entry = { path?: string; index?: boolean; children?: Entry[] };
  const leaves = (entries: readonly Entry[], base = ""): string[] => entries.flatMap((entry) => {
    const path = entry.index ? base || "/" : `${base}/${entry.path}`;
    return entry.children?.length ? leaves(entry.children, path) : [path];
  });
  const paths = leaves(routes as Entry[]);
  expect(paths).toEqual([
    "/", "/water", "/water/schedules", "/water/history", "/crops", "/crops/history", "/graphs", "/graphs/zones", "/graphs/lighting", "/graphs/statistics", "/graphs/scorecard", "/activity", "/activity/log",
    "/settings", "/band", "/equipment", "/plan", "/planner", "/planner/history", "/planner/experiments", "/auth/*", "/*",
  ]);
  expect(paths).not.toContain("/plants");
  expect(paths).not.toContain("/more");
});

test("in band: the shell reads API-01 and API-02 once each, shows no banner, and every route renders its head", async () => {
  serve("in-band");
  for (const [path] of SCREENS) {
    mount(path); await advance(0);
    expect(banners(), path).toEqual([]);
    expect(screen.getAllByRole("heading", { level: 1 }), path).toHaveLength(1);
    cleanup();
  }
  requests = [];
  mount("/"); await advance(0);
  // Now (#1129) reads the shell's API-02 poll, so one request serves both; it adds its water and alert reads, and below
  // them Coming up's plan and schedules, the lights' equipment and settings (#1340), and the zones' metric list and series
  // (#1138), whose bands follow once they draw.
  const shellReads = (path: string) => path === "/api/v1/me" || path === "/api/v1/now";
  const zoneReads = (path: string) => path.startsWith("/api/v1/series?") || path.startsWith("/api/v1/band?");
  expect(requests.filter(shellReads).sort()).toEqual(["/api/v1/me", "/api/v1/now"]);
  expect(requests.filter((path) => !shellReads(path) && !zoneReads(path)).sort()).toEqual(["/api/v1/alerts?status=recent&limit=200", "/api/v1/equipment", "/api/v1/irrigation", "/api/v1/irrigation/schedules?days=7", "/api/v1/metrics", "/api/v1/plan?hours=24", "/api/v1/settings"]);
  // API-02 every 15 s, API-01 every 60 s: one poll each, never a second.
  requests = [];
  await advance(NOW_POLL_MS);
  expect(requests.filter(shellReads)).toEqual(["/api/v1/now"]);
  await advance(60_000 - NOW_POLL_MS);
  expect(requests.filter((path) => path === "/api/v1/me")).toHaveLength(1);
});

test("a server render carries no greenhouse data: no banner, count, role, name or API build, and no request", () => {
  serve("alerts-merged");
  const Stub = createRoutesStub([{ path: "/", Component: () => <AppShell build={BUILD} authError={null} />, children: screenRoutes() }]);
  const html = renderToString(<Stub initialEntries={["/activity"]} />);
  expect(html).toContain('aria-label="Verdify dashboard, home"');
  expect(html).toContain("fedcba987654");
  expect(html).toContain('action="/auth/logout"');
  expect(html).not.toMatch(/data-banner|nav-count|role-pill|account-name"|Jason|Operator|open alerts/);
  // Before API-01 answers the account button reads Account and still holds Sign out.
  expect(html).toContain("Account");
  expect(html).not.toContain(REVISION.slice(0, 12));
  expect(requests).toEqual([]);
});

test("the header: lockup, the seven sections with the count, the account button; the footer names both builds", async () => {
  serve("alerts-merged");
  mount("/activity"); await advance(0);
  const brand = screen.getByRole("link", { name: "Verdify dashboard, home" });
  expect(brand.getAttribute("href")).toBe("/");
  expect(brand.querySelectorAll(".brand-leaf, .brand-mark, .brand-dot")).toHaveLength(3);
  expect(text(brand)).toBe("erdify · Dashboard");
  const sections = within(nav("Sections")).getAllByRole("link");
  expect(sections.map(text)).toEqual(["Now", "Water", "Crops", "Graphs", "Activity 2 open alerts", "Control", "Planner"]);
  expect(sections.map((link) => link.getAttribute("href"))).toEqual(["/", "/water", "/crops", "/graphs", "/activity", "/settings", "/plan"]);
  expect(sections.filter((link) => link.getAttribute("aria-current") === "page").map(text)).toEqual(["Activity 2 open alerts"]);
  // The count is a pill filled by the worst open severity; the account button holds the role pill and the name.
  expect(nav("Sections").querySelector(".nav-count")?.getAttribute("data-severity")).toBe("warning");
  const account = document.querySelector<HTMLDetailsElement>("details.account-menu")!;
  expect(text(account.querySelector("summary .role-pill"))).toBe("Operator");
  expect(text(account.querySelector("summary .account-name"))).toBe("Jason");
  expect(account.open).toBe(false);
  expect([...account.querySelectorAll(".menu-panel > p")].map(text)).toEqual(["Signed in as Jason (jason)", "Operator: can change anything"]);
  const signOut = account.querySelector<HTMLFormElement>("form.signout-form")!;
  expect([signOut.method, signOut.getAttribute("action"), text(signOut)]).toEqual(["post", "/auth/logout", "Sign out"]);
  expect(document.querySelectorAll(".role-pill")).toHaveLength(1);
  const footer = document.querySelector("footer")!;
  expect(text(footer.querySelector(".build-line"))).toBe(`Verdify dashboard · build fedcba987654 · API ${REVISION.slice(0, 12)}`);
  // The time zone is stated once, here (D-UX-08): the eyebrows no longer carry it.
  expect(text(footer.querySelector(".footer-zone"))).toBe("Times are America/Denver");
  expect(within(footer).getAllByRole("link").map((link) => link.getAttribute("href"))).toEqual(["https://graphs.verdify.ai/", "https://lab.verdify.ai/", "/fonts/OFL-sans.txt", "/fonts/OFL-mono.txt"]);
});

test("the phone bar holds the same seven, each an icon over its label; Control owns Settings and Equipment, Planner owns Plan", async () => {
  serve("alerts-merged");
  for (const [path, section] of [["/settings", "Control"], ["/equipment", "Control"], ["/plan", "Planner"], ["/planner/history", "Planner"], ["/planner/experiments", "Planner"]] as const) {
    mount(path); await advance(0);
    const tabs = within(nav("Sections, phone")).getAllByRole("link");
    expect(tabs.map(text), path).toEqual(["Now", "Water", "Crops", "Graphs", "Activity 2 open alerts", "Control", "Planner"]);
    expect(tabs.map((tab) => tab.getAttribute("href"))).toEqual(["/", "/water", "/crops", "/graphs", "/activity", "/settings", "/plan"]);
    expect(tabs.filter((tab) => tab.getAttribute("aria-current") === "page").map(text), path).toEqual([section]);
    expect(tabs.map((tab) => tab.querySelectorAll("svg.tab-icon[aria-hidden=true]").length)).toEqual([1, 1, 1, 1, 1, 1, 1]);
    const current = within(nav("Sections")).getAllByRole("link").filter((link) => link.getAttribute("aria-current") === "page");
    expect(current.map(text), path).toEqual([section]);
    cleanup();
  }
  // Sub-views mark their own section: the log is Activity, Statistics is Graphs.
  for (const [path, section] of [["/activity/log", "Activity 2 open alerts"], ["/graphs/statistics", "Graphs"], ["/water/history", "Water"]] as const) {
    mount(path); await advance(0);
    expect(within(nav("Sections, phone")).getAllByRole("link").filter((tab) => tab.getAttribute("aria-current") === "page").map(text), path).toEqual([section]);
    cleanup();
  }
});

test("with no open alert the count draws no pill, and a screen reader still hears 0 open alerts", async () => {
  serve("in-band");
  mount("/"); await advance(0);
  const activity = within(nav("Sections")).getByRole("link", { name: /^Activity/ });
  expect(text(activity)).toBe("Activity 0 open alerts");
  expect(activity.querySelector(".nav-count")).toBeNull();
});

test("every page has a page header: the eyebrow names the section, the h1 the view, and a tabbed section its tabs", async () => {
  serve("in-band");
  const expected: Record<string, [string, string, string[]]> = {
    "/water": ["Water", "Water today", ["Today", "Schedules", "History"]], "/water/schedules": ["Water", "Schedules", ["Today", "Schedules", "History"]],
    "/water/history": ["Water", "Water history", ["Today", "Schedules", "History"]], "/graphs": ["Graphs", "Climate", ["Climate", "Zones", "Lighting", "Statistics"]],
    "/graphs/zones": ["Graphs", "Zones", ["Climate", "Zones", "Lighting", "Statistics"]], "/graphs/lighting": ["Graphs", "Lighting", ["Climate", "Zones", "Lighting", "Statistics"]], "/graphs/statistics": ["Graphs", "Statistics", ["Climate", "Zones", "Lighting", "Statistics"]],
    "/activity": ["Activity", "Alerts", ["Alerts", "Log"]], "/activity/log": ["Activity", "Activity log", ["Alerts", "Log"]],
    "/settings": ["Control", "Settings", ["Settings", "Band", "Equipment"]], "/equipment": ["Control", "Equipment", ["Settings", "Band", "Equipment"]],
    "/band": ["Control", "Band", ["Settings", "Band", "Equipment"]],
    "/plan": ["Planner", "Plan", ["Plan", "History", "Experiments"]], "/planner/history": ["Planner", "Plan history", ["Plan", "History", "Experiments"]],
    "/planner/experiments": ["Planner", "Experiments", ["Plan", "History", "Experiments"]],
  };
  for (const [path, [eyebrow, h1, tabs]] of Object.entries(expected)) {
    mount(path); await advance(0);
    expect(text(document.querySelector(".page-crumbs")), path).toBe(eyebrow);
    expect(screen.getByRole("heading", { level: 1 }).textContent, path).toBe(h1);
    const views = screen.getByRole("navigation", { name: `${eyebrow} views` });
    expect(within(views).getAllByRole("link").map(text), path).toEqual(tabs);
    expect(within(views).getAllByRole("link").filter((tab) => tab.getAttribute("aria-current") === "page").map((tab) => tab.getAttribute("href")), path).toEqual([path]);
    cleanup();
  }
});

test("a heartbeat 4 min old reads Ingestor not running on every page, in a row of the sticky header", async () => {
  serve("ingestor-not-running");
  for (const [path] of SCREENS) {
    mount(path); await advance(0);
    expect(banners(), path).toEqual([{ tone: "fault", text: "Ingestor not running since 10:38. The controller keeps running on its own. Data on Now ›" }]);
    expect(document.querySelector("header.site-header .header-fault [data-banner=fault]"), path).not.toBeNull();
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

test("while the API is down the count is unknown: both navs drop it rather than keep the last one, and it returns with the API", async () => {
  serve("alerts-merged");
  mount("/graphs"); await advance(0);
  const activity = (label: string) => text(within(nav(label)).getByRole("link", { name: /^Activity/ }));
  expect([activity("Sections"), activity("Sections, phone")]).toEqual(["Activity 2 open alerts", "Activity 2 open alerts"]);
  serve("alerts-merged", { fail: { "API-02": 503 } });
  // One failure keeps the count; the second, after the 30 s backoff, is the API down.
  await advance(NOW_POLL_MS);
  expect(activity("Sections")).toBe("Activity 2 open alerts");
  await advance(2 * NOW_POLL_MS);
  expect(banners()).toEqual([{ tone: "fault", text: "Dashboard API unavailable" }]);
  expect([activity("Sections"), activity("Sections, phone")]).toEqual(["Activity", "Activity"]);
  // The next poll, after the 60 s backoff, answers again.
  serve("alerts-merged");
  await advance(60_000);
  expect(banners()).toEqual([]);
  expect(activity("Sections")).toBe("Activity 2 open alerts");
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
  // Warn banners sit in flow at the top of the page, never in the header.
  expect(document.querySelectorAll("main [data-banner=warn]")).toHaveLength(2);
  expect(document.querySelector("header [data-banner]")).toBeNull();
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

test("a 401 from a write shows the same card and stops every poll", async () => {
  serve("in-band", { fail: { "API-10": 401 } });
  mount("/write?from=drawer"); await advance(0);
  expect(screen.queryByRole("alertdialog")).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send the write" })); });
  await advance(0);
  expect(requests).toContain("/api/v1/settings/mister_engage_kpa");
  const card = screen.getByRole("alertdialog", { name: "Your session ended" });
  expect(within(card).getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/auth/login?returnTo=%2Fwrite%3Ffrom%3Ddrawer");
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
  expect(document.querySelector(".role-pill, .account-menu, [data-banner]")).toBeNull();
  expect(text(document.querySelector(".build-line"))).toBe("Verdify dashboard · build fedcba987654 · API —");
  expect(screen.getByText(/Request id/).textContent).toBe("Request id 4f9d2c1e-7b8a-4c3d-9e2f-1a2b3c4d5e6f");
  expect(requests).toEqual([]);
});

test("a refusal card's lockup is a full page load: a client navigation would keep the refusal and draw it at /", async () => {
  serve("in-band");
  const authError: AuthError = { reason: "failed", requestId: null };
  // As root.tsx: the refusal comes from the root loader, which never revalidates on a navigation.
  function Root() {
    const { pathname } = useLocation();
    return <><AppShell build={BUILD} authError={useLoaderData<{ authError: AuthError | null }>().authError} /><output data-testid="path">{pathname}</output></>;
  }
  const Stub = createRoutesStub([{
    id: "root", path: "/", Component: Root, loader: () => ({ authError }), shouldRevalidate: () => false,
    children: [{ index: true, Component: Now }, { path: "auth/*", Component: () => null }],
  }]);
  render(<Stub initialEntries={["/auth/callback"]} />); await advance(0);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Sign-in didn't complete");
  let prevented: boolean | null = null;
  // Runs after React's handlers, then stops jsdom's own navigation, which it does not implement.
  const record = (event: Event) => { prevented = event.defaultPrevented; event.preventDefault(); };
  document.addEventListener("click", record);
  await act(async () => { fireEvent.click(screen.getByRole("link", { name: "Verdify dashboard, home" })); });
  document.removeEventListener("click", record);
  // The browser loads / itself, so the BFF starts a sign-in; React Router never navigated.
  expect(prevented).toBe(false);
  expect(screen.getByTestId("path").textContent).toBe("/auth/callback");
});
