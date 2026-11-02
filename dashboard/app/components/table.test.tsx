// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Table, type TableColumn } from "./table";
import { renderUi, text } from "./test-render";

afterEach(cleanup);
type Row = { param: string; label: string; group: string };
const ROWS: Row[] = [
  { param: "sw_fog_enable", label: "Fog enable", group: "controller_safety" },
  { param: "mister_engage_kpa", label: "VPD Mister Engage", group: "planner_policy" },
  { param: "site_pressure_hpa", label: "Site pressure", group: "read_only" },
  { param: "mister_pulse_gap_s", label: "Mister pulse gap", group: "planner_policy" },
];
const COLUMNS: TableColumn<Row>[] = [{ key: "label", header: "Setting", rowHeader: true, cell: (row) => row.label }, { key: "param", header: "Key", cell: (row) => row.param }];
const ORDER = ["planner_policy", "scheduled_policy", "controller_safety"];
const table = (extra: Partial<Parameters<typeof Table<Row>>[0]> = {}) => <Table<Row> label="Settings" caption="Every setting" columns={COLUMNS} rows={ROWS} rowKey={(row) => row.param}
  group={(row) => row.group} groupOrder={ORDER} filter={{ label: "Filter settings", text: (row) => `${row.label} ${row.param}` }} {...extra} />;
const rowHeaders = (container: HTMLElement) => [...container.querySelectorAll("tbody th[scope=row]")].map(text);

test("a focusable, labelled scroll region around a table with a caption", () => {
  const { container } = renderUi(table());
  const region = screen.getByRole("region", { name: "Settings" });
  expect(region.className).toBe("table-wrap");
  expect(region.getAttribute("tabindex")).toBe("0");
  expect(text(container.querySelector("caption"))).toBe("Every setting");
  expect([...container.querySelectorAll("thead th")].map((th) => th.getAttribute("scope"))).toEqual(["col", "col"]);
});

test("rows keep a fixed group order, whatever order they arrive in; an unnamed group comes last", () => {
  const { container } = renderUi(table());
  expect([...container.querySelectorAll(".table-group th")].map(text)).toEqual(["planner_policy", "controller_safety", "read_only"]);
  expect([...container.querySelectorAll(".table-group th")].every((th) => th.getAttribute("scope") === "rowgroup" && th.getAttribute("colspan") === "2")).toBe(true);
  expect(rowHeaders(container)).toEqual(["VPD Mister Engage", "Mister pulse gap", "Fog enable", "Site pressure"]);
});

test("the text filter narrows the rows and says when nothing matches", () => {
  const { container } = renderUi(table());
  const filter = screen.getByLabelText("Filter settings");
  fireEvent.change(filter, { target: { value: "MISTER" } });
  expect(rowHeaders(container)).toEqual(["VPD Mister Engage", "Mister pulse gap"]);
  fireEvent.change(filter, { target: { value: "heat" } });
  expect(rowHeaders(container)).toEqual([]);
  expect(text(container)).toContain("Nothing matches “heat”.");
});

test("a controlled filter reports each change, so a screen can keep it in the URL", () => {
  const onChange = vi.fn();
  const { container } = renderUi(table({ filter: { label: "Filter settings", text: (row) => row.param, value: "fog", onChange } }));
  expect(rowHeaders(container)).toEqual(["Fog enable"]);
  fireEvent.change(screen.getByLabelText("Filter settings"), { target: { value: "site" } });
  expect(onChange).toHaveBeenCalledWith("site");
});

test("columns do not sort, and an empty table shows its empty words", () => {
  const { container } = renderUi(table());
  expect(container.querySelector("[aria-sort]")).toBeNull();
  expect(container.querySelector("thead button")).toBeNull();
  cleanup();
  expect(text(renderUi(table({ rows: [], empty: <p>No settings yet</p> })).container)).toContain("No settings yet");
});
