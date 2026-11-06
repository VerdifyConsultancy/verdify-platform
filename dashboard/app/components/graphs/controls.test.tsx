// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Graphs' controls (redesign §5.5): the custom range writes local days to the URL and refuses more than 400 days; the
// chips of what the charts draw take one off with ×, a setting's chip opens its drawer on Settings; Preset sets a
// group's metrics; Layers toggles the overlay, the envelope, each event source and the comparison, all in the URL.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createRoutesStub, useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { graphHref, graphWindow, parseGraphUrl, resolveView, withWindow } from "../../lib/graph-url";
import type { MetricDef, Setting } from "../../lib/operator-api";
import { text } from "../test-render";
import { ChartChips, GraphNotices, LayersMenu, PresetMenu, RangeForm, rangeDays, viewFields } from "./controls";

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
  const view = resolved.view;
  return <>
    <RangeForm initial={rangeDays(view.window, graphWindow(view.window, NOW))} action="/graphs" hidden={viewFields(view)} href={(from, to) => graphHref(withWindow(view, { kind: "days", from, to }))} />
    <ChartChips view={view} settings={resolved.settings} picked={resolved.picked} catalog={METRICS} />
    <PresetMenu view={view} catalog={METRICS} />
    <LayersMenu view={view} long={false} />
    <Where />
  </>;
}
function mount(path: string) {
  const Stub = createRoutesStub([{ path: "/graphs", Component: Controls }, { path: "/settings", Component: Where }]);
  render(<Stub initialEntries={[path]} />);
}
const where = () => screen.getByTestId("where").textContent;
const chips = (name: string) => within(screen.getByRole("list", { name })).getAllByRole("link");

test("the custom range opens on the window's local days and Show writes from and to, keeping the rest of the view", () => {
  mount("/graphs?range=7d&set=mister_engage_kpa&m=climate.flow_gpm&cmp=day");
  const form = screen.getByRole("form", { name: "Custom range" });
  expect((within(form).getByLabelText("From") as HTMLInputElement).value).toBe("2026-10-26");
  expect((within(form).getByLabelText("To") as HTMLInputElement).value).toBe("2026-11-02");
  fireEvent.change(within(form).getByLabelText("From"), { target: { value: "2026-10-31" } });
  fireEvent.change(within(form).getByLabelText("To"), { target: { value: "2026-11-01" } });
  fireEvent.click(within(form).getByRole("button", { name: "Show" }));
  expect(where()).toBe("/graphs?from=2026-10-31&to=2026-11-01&set=mister_engage_kpa&m=climate.flow_gpm&cmp=day");
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

test("without script the form submits the same query: from, to and every other part of the view as hidden fields", () => {
  mount("/graphs?range=24h&preset=water-soil&set=&m=climate.temp_south&outdoor=0&events=alerts&env=0&cmp=week");
  const form = screen.getByRole("form", { name: "Custom range" }) as HTMLFormElement;
  expect([form.method, new URL(form.action).pathname]).toEqual(["get", "/graphs"]);
  expect([...new FormData(form).entries()]).toEqual([
    ["from", "2026-11-01"], ["to", "2026-11-02"], ["preset", "water-soil"], ["set", ""], ["m", "climate.temp_south"], ["outdoor", "0"], ["events", "alerts"], ["env", "0"], ["cmp", "week"],
  ]);
  expect(rangeDays({ kind: "exact", from: "a", to: "b", start: NOW - 7200, end: NOW }, graphWindow({ kind: "exact", from: "a", to: "b", start: NOW - 7200, end: NOW }, NOW))).toEqual({ from: "2026-11-02", to: "2026-11-02" });
});

test("the chips: a setting chip opens its drawer on Settings and × takes it off; a metric chip's × takes it off", () => {
  // No setting draws by default (#1354): these two are asked for by name.
  mount("/graphs?range=72h&set=mister_engage_kpa&set=fog_rh_ceiling_pct&m=climate.flow_gpm");
  const settings = screen.getByRole("list", { name: "Settings drawn as steps" });
  // Each chip reads its setting by its label (the words layer), then the panel it draws on; its key is its title.
  expect(within(settings).getAllByRole("listitem").map(text)).toEqual(["VPD Mister Engage · VPD×", "Fog RH Ceiling · RH×"]);
  const chip = within(settings).getByRole("link", { name: "VPD Mister Engage · VPD" });
  expect([chip.getAttribute("href"), chip.getAttribute("title")]).toEqual(["/settings?edit=mister_engage_kpa", "mister_engage_kpa: edit it on Settings"]);
  // Taking one off keeps the other in the URL.
  expect(within(settings).getByRole("link", { name: "Take VPD Mister Engage off the charts" }).getAttribute("href")).toBe("/graphs?range=72h&set=fog_rh_ceiling_pct&m=climate.flow_gpm");
  const metrics = screen.getByRole("list", { name: "Metrics on the charts" });
  expect(within(metrics).getAllByRole("listitem").map(text)).toEqual(["Site water flow · gal/min×"]);
  fireEvent.click(within(metrics).getByRole("link", { name: "Take Site water flow off the charts" }));
  expect(where()).toBe("/graphs?range=72h&set=mister_engage_kpa&set=fog_rh_ceiling_pct");
  fireEvent.click(within(screen.getByRole("list", { name: "Settings drawn as steps" })).getByRole("link", { name: "Take Fog RH Ceiling off the charts" }));
  expect(where()).toBe("/graphs?range=72h&set=mister_engage_kpa");
  fireEvent.click(screen.getByRole("link", { name: "Take VPD Mister Engage off the charts" }));
  expect(where()).toBe("/graphs?range=72h&set=");
  expect(screen.queryByRole("list", { name: "Settings drawn as steps" })).toBeNull();
});

test("Preset: each a link that sets the preset and takes the picked metrics off; a group with nothing reads none yet", () => {
  mount("/graphs?range=7d&m=climate.flow_gpm");
  expect(text(document.querySelector(".gr-menu > summary"))).toBe("Preset Climate");
  expect(chips("Presets").map((link) => [text(link), link.getAttribute("href"), link.getAttribute("aria-current")])).toEqual([
    ["Climate", "/graphs?range=7d", "true"], ["Water and soil", "/graphs?range=7d&preset=water-soil", null],
    ["Light · none yet", "/graphs?range=7d&preset=light", null], ["Controller", "/graphs?range=7d&preset=controller", null],
  ]);
  fireEvent.click(screen.getByRole("link", { name: "Water and soil" }));
  expect(where()).toBe("/graphs?range=7d&preset=water-soil");
  // A preset's metric chip turns the preset into the metrics left.
  expect(within(screen.getByRole("list", { name: "Metrics on the charts" })).getByRole("link", { name: "Take South soil moisture 1 off the charts" }).getAttribute("href")).toBe("/graphs?range=7d&m=climate.flow_gpm");
});

test("Layers: the overlay and the envelope, each event source and the comparison are chips in the URL; the menu stays open while they change", () => {
  mount("/graphs?range=24h");
  expect(text(document.querySelector(".gr-layers > summary"))).toBe("Layers");
  expect(chips("Overlays").map((link) => [link.textContent, link.getAttribute("aria-current"), link.getAttribute("href")])).toEqual([
    ["Outdoor", "true", "/graphs?range=24h&outdoor=0"], ["Min–max", "true", "/graphs?range=24h&env=0"],
  ]);
  expect(chips("Compare with").map((link) => [link.textContent, link.getAttribute("aria-current"), link.getAttribute("href")])).toEqual([
    ["None", "true", "/graphs?range=24h"], ["Yesterday", null, "/graphs?range=24h&cmp=day"], ["Last week", null, "/graphs?range=24h&cmp=week"],
  ]);
  expect(screen.getByRole("list", { name: "Event sources" }).closest("[data-menu-keep]")).not.toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Yesterday" }));
  expect(where()).toBe("/graphs?range=24h&cmp=day");
  fireEvent.click(screen.getByRole("link", { name: "Runs" }));
  expect(where()).toBe("/graphs?range=24h&events=people,iris,agents,system,alerts&cmp=day");
  expect(text(document.querySelector(".gr-layers .gr-menu-value"))).toBe(" · 2 changed");
  // Turning the last one back on is every source again: no parameter.
  fireEvent.click(screen.getByRole("link", { name: "Runs" }));
  expect(where()).toBe("/graphs?range=24h&cmp=day");
});

test("notices list what the page dropped from the link", () => {
  const { container } = render(<GraphNotices notices={["Removed unknown metric climate.nope"]} />);
  expect(within(container).getByRole("list", { name: "Changes to this link" }).textContent).toBe("Removed unknown metric climate.nope");
  cleanup();
  expect(render(<GraphNotices notices={[]} />).container.innerHTML).toBe("");
});
