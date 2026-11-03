// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Graphs' controls (#1130): the custom range writes local days to the URL and refuses more than 400 days; what is on
// the charts is a row of chips whose × takes one off, a setting's chip opens its drawer on Settings, and Scorecard ›.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { graphWindow, parseGraphUrl, resolveView } from "../../lib/graph-url";
import type { MetricDef, Setting } from "../../lib/operator-api";
import { text } from "../test-render";
import { GraphNotices, OnCharts, RangeForm, rangeDays } from "./controls";

const NOW = Date.parse("2026-11-02T17:42:00Z") / 1000;
const SET = fixture("in-band", { now: NOW * 1000 });
const METRICS = SET["API-03"].items as MetricDef[];
const SETTINGS = SET["API-09"].items as Setting[];
afterEach(cleanup);

function Where() {
  const { pathname, search } = useLocation();
  return <output data-testid="where">{`${pathname}${search}`}</output>;
}
function Controls() {
  const { search } = useLocation();
  const resolved = resolveView(parseGraphUrl(search).view, METRICS, SETTINGS);
  return <>
    <RangeForm view={resolved.view} window={graphWindow(resolved.view.window, NOW)} />
    <OnCharts view={resolved.view} settings={resolved.settings} picked={resolved.picked} units={resolved.units} metricsCatalog={METRICS} settingsCatalog={SETTINGS} />
    <Where />
  </>;
}
function mount(path: string) {
  const Stub = createRoutesStub([{ path: "/graphs", Component: Controls }, { path: "/settings", Component: Where }, { path: "/graphs/scorecard", Component: Where }]);
  render(<Stub initialEntries={[path]} />);
}
const where = () => screen.getByTestId("where").textContent;

test("the custom range opens on the window's local days and Show writes from and to, keeping the settings and metrics", () => {
  mount("/graphs?range=7d&set=mister_engage_kpa&m=climate.flow_gpm");
  const form = screen.getByRole("form", { name: "Custom range" });
  expect((within(form).getByLabelText("From") as HTMLInputElement).value).toBe("2026-10-26");
  expect((within(form).getByLabelText("To") as HTMLInputElement).value).toBe("2026-11-02");
  fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-10-31" } });
  fireEvent.change(within(form).getByLabelText("To"), { target: { value: "2026-11-01" } });
  fireEvent.click(within(form).getByRole("button", { name: "Show" }));
  expect(where()).toBe("/graphs?from=2026-10-31&to=2026-11-01&set=mister_engage_kpa&m=climate.flow_gpm");
  // The new window starts the fields over from it.
  expect((within(screen.getByRole("form", { name: "Custom range" })).getByLabelText("From") as HTMLInputElement).value).toBe("2026-10-31");
});

test("more than 400 days is refused beside the fields: Up to 400 days", () => {
  mount("/graphs?range=24h");
  const form = screen.getByRole("form", { name: "Custom range" });
  fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2025-09-28" } });
  fireEvent.change(within(form).getByLabelText("To"), { target: { value: "2026-11-02" } });
  fireEvent.click(within(form).getByRole("button", { name: "Show" }));
  expect(within(form).getByRole("alert").textContent).toBe("Up to 400 days");
  expect(within(form).getByLabelText("From").getAttribute("aria-invalid")).toBe("true");
  expect(where()).toBe("/graphs?range=24h");
  fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2025-09-29" } });
  fireEvent.click(within(form).getByRole("button", { name: "Show" }));
  expect(where()).toBe("/graphs?from=2025-09-29&to=2026-11-02");
});

test("without script the form submits the same query: from, to and the hidden set and m", () => {
  mount("/graphs?range=24h&set=&m=climate.flow_gpm");
  const form = screen.getByRole("form", { name: "Custom range" }) as HTMLFormElement;
  expect([form.method, new URL(form.action).pathname]).toEqual(["get", "/graphs"]);
  expect([...new FormData(form).entries()]).toEqual([["from", "2026-11-01"], ["to", "2026-11-02"], ["set", ""], ["m", "climate.flow_gpm"]]);
  expect(rangeDays({ kind: "exact", from: "a", to: "b", start: NOW - 7200, end: NOW }, graphWindow({ kind: "exact", from: "a", to: "b", start: NOW - 7200, end: NOW }, NOW))).toEqual({ from: "2026-11-02", to: "2026-11-02" });
});

test("on the charts: a setting chip opens its drawer on Settings and × takes it off; a metric chip's × takes it off", () => {
  mount("/graphs?range=72h&m=climate.flow_gpm");
  const settings = screen.getByRole("list", { name: "Settings drawn as steps" });
  expect(within(settings).getAllByRole("listitem").map(text)).toEqual(["VPD · mister_engage_kpa×", "RH · fog_rh_ceiling_pct×"]);
  expect(within(settings).getByRole("link", { name: "VPD · mister_engage_kpa" }).getAttribute("href")).toBe("/settings?edit=mister_engage_kpa");
  // Taking one default off names the other, so the URL keeps it.
  expect(within(settings).getByRole("link", { name: "Take mister_engage_kpa off the charts" }).getAttribute("href")).toBe("/graphs?range=72h&set=fog_rh_ceiling_pct&m=climate.flow_gpm");
  const metrics = screen.getByRole("list", { name: "Metrics on the charts" });
  expect(within(metrics).getAllByRole("listitem").map(text)).toEqual(["Site water flow · gal/min×"]);
  fireEvent.click(within(metrics).getByRole("link", { name: "Take Site water flow off the charts" }));
  expect(where()).toBe("/graphs?range=72h");
  fireEvent.click(within(screen.getByRole("list", { name: "Settings drawn as steps" })).getByRole("link", { name: "Take fog_rh_ceiling_pct off the charts" }));
  expect(where()).toBe("/graphs?range=72h&set=mister_engage_kpa");
  fireEvent.click(screen.getByRole("link", { name: "Take mister_engage_kpa off the charts" }));
  expect(where()).toBe("/graphs?range=72h&set=");
  expect(screen.queryByRole("list", { name: "Settings drawn as steps" })).toBeNull();
  expect(screen.getByRole("link", { name: "Scorecard" }).getAttribute("href")).toBe("/graphs/scorecard");
  fireEvent.click(screen.getByRole("link", { name: "Scorecard" }));
  expect(where()).toBe("/graphs/scorecard");
});

test("notices list what the page dropped from the link", () => {
  const { container } = render(<GraphNotices notices={["Removed unknown metric climate.nope"]} />);
  expect(within(container).getByRole("list", { name: "Changes to this link" }).textContent).toBe("Removed unknown metric climate.nope");
  cleanup();
  expect(render(<GraphNotices notices={[]} />).container.innerHTML).toBe("");
});
