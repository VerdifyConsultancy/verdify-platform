// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// "+ Metric" and "+ Setting" (#1130): the dialogs list only what Graphs can draw, every choice is a link that changes
// the URL, the picker stops at 8 metrics per window, and nothing is hard-coded: the keys are API-03's.
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../../e2e/fixtures/index.mjs";
import { CAP_TEXT, parseGraphUrl, resolveView } from "../../lib/graph-url";
import type { MetricDef, Setting } from "../../lib/operator-api";
import { renderUi, text } from "../test-render";
import { MetricPicker, cadenceWords, metricFacts, pickerGroups } from "./metric-picker";
import { SettingChooser, chooserGroups, settingChipText } from "./setting-chooser";

const SET = fixture("in-band", { now: Date.parse("2026-11-02T17:42:00Z") });
const METRICS = SET["API-03"].items as MetricDef[];
const SETTINGS = SET["API-09"].items as Setting[];
const dialog = () => document.querySelector("dialog")!;
afterEach(cleanup);

function picker(search = "") {
  const resolved = resolveView(parseGraphUrl(search).view, METRICS, SETTINGS);
  renderUi(<MetricPicker view={resolved.view} metrics={METRICS} picked={resolved.picked} />);
  fireEvent.click(screen.getByRole("button", { name: "+ Metric" }));
}

test("groups every metric but the daily keys in the picker's order, and the filter matches label, key, unit and note", () => {
  const groups = pickerGroups(METRICS);
  expect(groups.map((group) => group.group)).toEqual(["house climate", "zones", "water and soil", "outdoor", "controller"]);
  expect(groups.flatMap((group) => group.items).some((metric) => metric.key.startsWith("daily."))).toBe(false);
  expect(pickerGroups(METRICS, "gal/min").flatMap((group) => group.items.map((metric) => metric.key))).toEqual(["climate.flow_gpm"]);
  expect(pickerGroups(METRICS, "SOUTH").flatMap((group) => group.items.map((metric) => metric.key))).toEqual(["climate.temp_south", "climate.soil_moisture_south_1"]);
  // A group the picker does not name follows the named ones.
  const extra = { ...METRICS[0], key: "climate.hydro_ph", label: "Hydro pH", group: "hydro", unit: "pH" };
  expect(pickerGroups([...METRICS, extra]).map((group) => group.group).at(-1)).toBe("hydro");
  expect([60, 300, 3600, 86_400, 45].map(cadenceWords)).toEqual(["every 60 s", "every 5 min", "every hour", "every day", "every 45 s"]);
  expect(metricFacts(METRICS.find((metric) => metric.key === "climate.flow_gpm")!)).toBe("climate.flow_gpm · gal/min · every 60 s · MEASURED");
});

test("a choice links to the view plus m=<key>; the climate traces and picked metrics read On the charts", () => {
  picker("range=7d&set=mister_engage_kpa&m=climate.flow_gpm");
  const list = within(dialog());
  expect(document.activeElement).toBe(list.getByLabelText("Filter metrics"));
  expect(list.getByRole("link", { name: /South soil moisture 1/ }).getAttribute("href")).toBe("/graphs?range=7d&set=mister_engage_kpa&m=climate.flow_gpm&m=climate.soil_moisture_south_1");
  expect(list.queryByRole("link", { name: /Site water flow/ })).toBeNull();
  expect(list.queryByRole("link", { name: /Indoor temperature/ })).toBeNull();
  expect(text(dialog())).toContain("Indoor temperatureclimate.temp_avg · °F · every 60 s · MEASUREDOn the charts");
  expect(text(dialog())).not.toContain("daily.");
  fireEvent.change(list.getByLabelText("Filter metrics"), { target: { value: "heap" } });
  expect(list.getAllByRole("link").map((link) => link.textContent)).toEqual(["Controller free heapdiagnostics.heap_free_kb · kB · every 60 s · MEASURED"]);
  fireEvent.change(list.getByLabelText("Filter metrics"), { target: { value: "lux" } });
  expect(text(dialog())).toContain("Nothing matches “lux”.");
});

test("at 8 metrics per window the picker says so and offers no more", () => {
  picker(["climate.temp_south", "climate.soil_moisture_south_1", "climate.flow_gpm", "climate.outdoor_temp_f", "climate.outdoor_rh_pct"].map((key) => `m=${key}`).join("&"));
  expect(within(dialog()).getByRole("status").textContent).toBe(CAP_TEXT);
  expect(within(dialog()).queryAllByRole("link")).toHaveLength(0);
});

test("+ Setting lists numeric settings in an open panel's unit, never the Band in force rows, grouped by panel", () => {
  const units = ["°F", "kPa", "%"];
  const groups = chooserGroups(SETTINGS, units);
  expect(groups.map((group) => [group.unit, group.items.map((row) => row.param)])).toEqual([
    ["°F", ["temp_low"]],
    ["kPa", ["mister_engage_kpa", "band_vpd_low_sm"]],
    ["%", ["fog_rh_ceiling_pct", "sched_mist_rh_max_pct"]],
  ]);
  expect(chooserGroups(SETTINGS, ["kPa"], "engage").flatMap((group) => group.items.map((row) => row.param))).toEqual(["mister_engage_kpa"]);
  expect(settingChipText(SETTINGS.find((row) => row.param === "mister_engage_kpa")!)).toBe("VPD · mister_engage_kpa");
  const resolved = resolveView(parseGraphUrl("range=72h&set=mister_engage_kpa").view, METRICS, SETTINGS);
  renderUi(<SettingChooser view={resolved.view} settings={SETTINGS} chosen={resolved.settings} units={resolved.units} />);
  fireEvent.click(screen.getByRole("button", { name: "+ Setting" }));
  const list = within(dialog());
  expect(list.getByRole("link", { name: /Fog RH Ceiling/ }).getAttribute("href")).toBe("/graphs?range=72h&set=mister_engage_kpa&set=fog_rh_ceiling_pct");
  expect(text(list.getByRole("link", { name: /Fog RH Ceiling/ }))).toBe("Fog RH Ceilingfog_rh_ceiling_pct · % · device 90.0%");
  expect(text(dialog())).toContain("VPD Mister Engagemister_engage_kpa · kPa · device 1.60 kPaOn the charts");
  expect(text(dialog())).not.toMatch(/band_house|band_center|sw_gl_main_auto_mode|mister_pulse_gap_s/);
  fireEvent.click(list.getByRole("button", { name: "Close" }));
  expect(dialog().open).toBe(false);
});
