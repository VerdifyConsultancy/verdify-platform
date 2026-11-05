// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Line options (#1182) against #1122's fixture API, at 10:42 on Monday 2026-11-02.
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { NOW, OPERATOR, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { LineOptions, OptionsButton } from "./line-options";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

const lines = (names: string | string[] = "in-band") => (fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus).lines;
const Where = () => <span data-testid="where">{useLocation().search}</span>;
function open(path: string, session: Partial<Session> = OPERATOR, names: string | string[] = "in-band") {
  const api = startFixtureApi({ fixture: names });
  renderWithApi(<><LineOptions lines={lines(names)} /><Where /></>, session, path);
  return api;
}
const drawer = () => within(screen.getByRole("dialog"));
const type = (label: string, value: string) => fireEvent.change(drawer().getByLabelText(label), { target: { value } });
const save = () => fireEvent.click(drawer().getByRole("button", { name: "Save" }));

test("L04: cap 130 and reserve 60 send {daily_cap_min: 130, vpd_reserve_min: 60, manual: true} and close", async () => {
  const api = open("/water?options=L04");
  expect(drawer().getByLabelText<HTMLInputElement>("Daily cap").value).toBe("120");
  expect(drawer().getByLabelText<HTMLInputElement>("VPD reserve").value).toBe("60");
  type("Daily cap", "130");
  save();
  await advance(0);
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/lines/L04", { daily_cap_min: 130, vpd_reserve_min: 60, manual: true }]]);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByTestId("where").textContent).toBe("");
  expect(text(screen.getByRole("status"))).toContain("Requested: L04 daily cap 130 min, 60 min kept for VPD");
});

test("today's minutes and the headroom sit beside the cap and follow what is typed; 0 means none", () => {
  open("/water?options=L04");
  const headroom = () => text(document.querySelector("[data-headroom]"));
  expect(headroom()).toBe("Today 14 of 120 min · 106 min left");
  type("Daily cap", "130");
  expect(headroom()).toBe("Today 14 of 130 min · 116 min left");
  type("Daily cap", "0");
  expect(headroom()).toBe("Today 14 min · no cap");
  type("Daily cap", "abc");
  expect(headroom()).toBe("Today 14 min");
  expect(text(screen.getByRole("dialog"))).toContain("0 means none");
});

test("a line that is not a mister zone has no reserve, and sends none; Manual unticked and a reason go with it", async () => {
  const api = open("/water?options=L01");
  expect(drawer().queryByLabelText("VPD reserve")).toBeNull();
  type("Daily cap", "90");
  fireEvent.click(drawer().getByRole("checkbox", { name: "Manual: Iris skips this" }));
  type("Reason (optional)", "Hot week");
  save();
  await advance(0);
  expect(writes(api).map((row) => row.body)).toEqual([{ daily_cap_min: 90, manual: false, reason: "Hot week" }]);
});

test("the hint flags a reserve above the cap, the API refuses it with out_of_range, and it shows beside its field with the drawer open", async () => {
  const api = open("/water?options=L07");
  type("Daily cap", "30");
  expect(text(document.querySelector(".field-flag"))).toBe("");
  type("VPD reserve", "40");
  expect(text(document.querySelector(".field-flag"))).toBe("Above the 30 min cap: this will be refused");
  save();
  await advance(0);
  expect(writes(api).map((row) => row.body)).toEqual([{ daily_cap_min: 30, vpd_reserve_min: 40, manual: true }]);
  expect(screen.getByRole("dialog")).toBeTruthy();
  const field = drawer().getByLabelText<HTMLInputElement>("VPD reserve");
  expect(field.getAttribute("aria-invalid")).toBe("true");
  expect(field.closest(".field")?.querySelector(".field-error")?.textContent).toBe("Must be 0–30 min");
});

test("the client checks only whole minutes from 0 to 480: nothing is sent past them", async () => {
  const api = open("/water?options=L04");
  type("Daily cap", "481");
  save();
  await advance(0);
  expect(drawer().getByLabelText("Daily cap").closest(".field")?.querySelector(".field-error")?.textContent).toBe("Must be 0–480 min");
  type("Daily cap", "12.5");
  save();
  await advance(0);
  expect(drawer().getByLabelText("Daily cap").closest(".field")?.querySelector(".field-error")?.textContent).toBe("Enter whole minutes");
  type("Daily cap", "120");
  type("VPD reserve", "");
  save();
  await advance(0);
  expect(drawer().getByLabelText("VPD reserve").closest(".field")?.querySelector(".field-error")?.textContent).toBe("Enter whole minutes");
  expect(writes(api)).toEqual([]);
});

test("no drawer for a viewer, L03, L11 or a stray id; Options opens ?options=<line_id> for an operator, and neither a viewer nor changes off can press it", async () => {
  open("/water?options=L04", { role: "viewer" });
  expect(screen.queryByRole("dialog")).toBeNull();
  for (const id of ["L03", "L11", "L99"]) {
    cleanup(); stopFixtureApi();
    open(`/water?options=${id}`);
    expect(screen.queryByRole("dialog"), id).toBeNull();
  }
  cleanup(); stopFixtureApi();
  startFixtureApi();
  renderWithApi(<><OptionsButton line={{ line_id: "L04" }} /><LineOptions lines={lines()} /></>);
  fireEvent.click(screen.getByRole("button", { name: "Options… of L04" }));
  await advance(0);
  expect(screen.getByRole("dialog")).toBeTruthy();
  cleanup(); stopFixtureApi();
  renderWithApi(<OptionsButton line={{ line_id: "L04" }} />, { role: "viewer" });
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  renderWithApi(<OptionsButton line={{ line_id: "L04" }} />, { role: "operator", writesEnabled: false });
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Options… of L04" }).disabled).toBe(true);
});
