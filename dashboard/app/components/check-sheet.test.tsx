// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The check sheet (#1182) against #1122's fixture API, at 10:42 on Monday 2026-11-02.
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { NOW, OPERATOR, UUID, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { CheckSheet, checkAction } from "./check-sheet";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

const lines = (names: string | string[] = "lines-unchecked") => (fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus).lines;
const lineOf = (from: readonly IrrigationLine[], id: string) => from.find((line) => line.line_id === id)!;
const Where = () => <span data-testid="where">{useLocation().search}</span>;
function open(path: string, session: Partial<Session> = OPERATOR, names: string | string[] = "lines-unchecked", from = lines(names)) {
  const api = startFixtureApi({ fixture: names });
  renderWithApi(<><CheckSheet lines={from} /><Where /></>, session, path);
  return api;
}
const sheet = () => within(screen.getByRole("dialog"));
const where = () => screen.getByTestId("where").textContent;
const checked = { checked_by: "jason", checked_at: "2026-11-01T15:00:00Z", note: null };

test("what ?check= asks: enabling a disabled line, Schedule on an enabled mister, or nothing", () => {
  const from = lines();
  expect(checkAction(lineOf(from, "L02"), null)).toBe("enable");
  expect(checkAction(lineOf(from, "L04"), null)).toBe("schedule");
  expect(checkAction(lineOf(from, "L07"), null)).toBe("enable");
  // A disabled mister whose Schedule was pressed asks for Schedule, not the enable.
  expect(checkAction(lineOf(from, "L07"), "schedule")).toBe("schedule");
  expect(checkAction(lineOf(from, "L02"), "schedule")).toBeNull();
  expect(checkAction({ ...lineOf(from, "L04"), schedule_member: true }, null)).toBeNull();
  expect(checkAction({ ...lineOf(from, "L02"), check: checked }, null)).toBeNull();
  // L01 is enabled with no check: its check comes with its first schedule in the editor.
  expect(checkAction(lineOf(from, "L01"), null)).toBeNull();
});

test("enable: the line's check, a note up to 500, Manual ticked and Reason; Enable stays disabled until Checked is ticked, then sends once and closes", async () => {
  const api = open("/water?check=L02");
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Enable L02");
  const save = sheet().getByRole<HTMLButtonElement>("button", { name: "Enable" });
  expect(save.disabled).toBe(true);
  // Enter in a field cannot send it either.
  fireEvent.submit(screen.getByRole("dialog").querySelector("form")!);
  await advance(0);
  expect(writes(api)).toEqual([]);
  const box = sheet().getByRole<HTMLInputElement>("checkbox", { name: "Checked: line, tank and injector checked, no leaks" });
  expect(box.checked).toBe(false);
  expect(sheet().getByRole<HTMLInputElement>("checkbox", { name: "Manual: Iris skips this" }).checked).toBe(true);
  expect(sheet().getByLabelText<HTMLInputElement>("Note (optional)").maxLength).toBe(500);
  fireEvent.click(box);
  expect(save.disabled).toBe(false);
  fireEvent.change(sheet().getByLabelText("Note (optional)"), { target: { value: "  No leaks at the tank " } });
  fireEvent.change(sheet().getByLabelText("Reason (optional)"), { target: { value: "Feeding starts" } });
  fireEvent.click(save);
  fireEvent.click(save);
  await advance(0);
  const sent = writes(api);
  expect(sent.map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L02", { enabled: true, checked: true, check_note: "No leaks at the tank", manual: true, reason: "Feeding starts" }]]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(where()).toBe("");
  expect(screen.getByRole("status").textContent).toContain("Requested: L02 enabled, check recorded");
});

test("no note or reason sends neither; unticking Manual sends manual false", async () => {
  const api = open("/water?check=L02");
  fireEvent.click(sheet().getByRole("checkbox", { name: /^Checked:/ }));
  fireEvent.click(sheet().getByRole("checkbox", { name: "Manual: Iris skips this" }));
  fireEvent.click(sheet().getByRole("button", { name: "Enable" }));
  await advance(0);
  expect(writes(api).map((row) => row.body)).toEqual([{ enabled: true, checked: true, manual: false }]);
});

test("Schedule on: the sheet asks for the same check and sends schedule_member, not enabled", async () => {
  const api = open("/water?check=L04&for=schedule");
  expect(text(screen.getByRole("dialog").querySelector("h2"))).toBe("Turn on Schedule for L04");
  sheet().getByRole("checkbox", { name: "Checked: nozzles over the Vandas, coverage and walkway checked" });
  const save = sheet().getByRole<HTMLButtonElement>("button", { name: "Turn on Schedule" });
  expect(save.disabled).toBe(true);
  fireEvent.click(sheet().getByRole("checkbox", { name: /^Checked:/ }));
  fireEvent.change(sheet().getByLabelText("Note (optional)"), { target: { value: "Coverage good" } });
  fireEvent.click(save);
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { schedule_member: true, checked: true, check_note: "Coverage good", manual: true }]]);
  expect(where()).toBe("");
});

test("a refusal keeps the sheet open with the code in words; Cancel and × close it and send nothing", async () => {
  const api = open("/water?check=L02");
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lines-unchecked", fail: { "API-IRR-08": 503 } } });
  fireEvent.click(sheet().getByRole("checkbox", { name: /^Checked:/ }));
  fireEvent.click(sheet().getByRole("button", { name: "Enable" }));
  await advance(0);
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(sheet().getByRole("alert").textContent).not.toBe("");
  fireEvent.click(sheet().getByRole("button", { name: "Cancel" }));
  await advance(0);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(where()).toBe("");
});

test("no sheet for a viewer, a line with its check, L03, L11 or a stray id; with changes off an operator sees it disabled", async () => {
  open("/water?check=L02", { role: "viewer" });
  expect(screen.queryByRole("dialog")).toBeNull();
  cleanup(); stopFixtureApi();
  const from = lines().map((line) => (line.line_id === "L02" ? { ...line, check: checked } : line));
  open("/water?check=L02", OPERATOR, "lines-unchecked", from);
  expect(screen.queryByRole("dialog")).toBeNull();
  for (const id of ["L03", "L11", "L99", "all"]) {
    cleanup(); stopFixtureApi();
    open(`/water?check=${id}`);
    expect(screen.queryByRole("dialog"), id).toBeNull();
  }
  cleanup(); stopFixtureApi();
  open("/water?check=L02", { role: "operator", writesEnabled: false });
  expect(sheet().getByRole<HTMLInputElement>("checkbox", { name: /^Checked:/ }).closest("fieldset")?.disabled).toBe(true);
  expect(sheet().getByRole<HTMLButtonElement>("button", { name: "Enable" }).disabled).toBe(true);
});
