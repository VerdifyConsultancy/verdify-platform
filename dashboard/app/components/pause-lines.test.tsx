// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Pause and Resume (#1182, redesign §5.2) against #1122's fixture API, at 10:42 on Monday 2026-11-02: Water → Today's
// header actions, the layout's drawer, a row's Resume and a block's "until after this run".
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { NOW, OPERATOR, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { PAUSE_TEXT, PauseActions, PauseAfterRun, PauseDrawer, ResumeLine, defaultPauseUntil } from "./pause-lines";

/** Water → Today's header actions and the layout's drawer, as the Water layout renders them. */
function PauseControls({ lines: from }: { lines: readonly IrrigationLine[] | undefined }) {
  return <><PauseActions lines={from} /><PauseDrawer lines={from} /></>;
}

afterEach(() => { cleanup(); stopFixtureApi(); });

const lines = (names: string | string[] = "in-band") => (fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus).lines;
const Where = () => <span data-testid="where">{useLocation().search}</span>;
const where = () => screen.getByTestId("where").textContent;
function controls(path = "/water", session: Partial<Session> = OPERATOR, from: readonly IrrigationLine[] | null = lines()) {
  const api = startFixtureApi();
  renderWithApi(<><PauseControls lines={from ?? undefined} /><Where /></>, session, path);
  return api;
}
const drawer = () => within(screen.getByRole("dialog"));
const line = (index: number) => drawer().getByRole<HTMLOptionElement>("option", { name: new RegExp(`^${index === 0 ? "All lines" : `L0?${index}`}`) });
const AT_14 = "2026-11-02T21:00:00.000Z";

test("Pause lines… opens ?pause=all; All lines until 14:00 with a reason sends line_id all with {paused_until, reason} and no manual, once", async () => {
  const api = controls();
  fireEvent.click(screen.getByRole("button", { name: "Pause lines…" }));
  await advance(0);
  expect(where()).toBe("?pause=all");
  expect(drawer().getByText(PAUSE_TEXT)).toBeTruthy();
  expect(line(0).selected).toBe(true);
  // The drawer starts on the next whole hour at least 30 minutes ahead.
  const until = drawer().getByLabelText<HTMLInputElement>("Until");
  expect(until.value).toBe("2026-11-02T12:00");
  expect(new Date(defaultPauseUntil(NOW)).toISOString()).toBe("2026-11-02T19:00:00.000Z");
  fireEvent.change(until, { target: { value: "2026-11-02T14:00" } });
  fireEvent.change(drawer().getByLabelText("Reason (optional)"), { target: { value: "Rain today" } });
  const save = drawer().getByRole("button", { name: "Pause" });
  fireEvent.click(save);
  fireEvent.click(save);
  await advance(0);
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/lines/all", { paused_until: AT_14, reason: "Rain today" }]]);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(where()).toBe("");
  expect(screen.getByRole("status").textContent).toContain("Requested: all lines paused until 14:00");
});

test("one line, from the list or ?pause=<line_id>, with no reason; the list has All lines and the wet lines, never L03 or L11", async () => {
  const api = controls("/water?pause=L04");
  expect(drawer().getByLabelText<HTMLSelectElement>("Line").selectedOptions[0].textContent).toBe("Center mister");
  expect([...drawer().getByLabelText<HTMLSelectElement>("Line").options].map((option) => option.textContent)).toEqual(["All lines", "Wall drip", "Wall drip (fertilized)", "Center mister", "Center drip", "Center drip (fertilized)", "South mister", "South mister (fertilized)", "West mister", "West mister (fertilized)"]);
  fireEvent.change(drawer().getByLabelText("Until"), { target: { value: "2026-11-03T06:30" } });
  fireEvent.click(drawer().getByRole("button", { name: "Pause" }));
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { paused_until: "2026-11-03T13:30:00.000Z" }]]);
});

test("a time already past would resume, so it is refused beside the field and nothing is sent; so is no time", async () => {
  const api = controls("/water?pause=all");
  const until = () => drawer().getByLabelText<HTMLInputElement>("Until");
  fireEvent.change(until(), { target: { value: "2026-11-02T10:42" } });
  fireEvent.click(drawer().getByRole("button", { name: "Pause" }));
  await advance(0);
  expect(until().closest(".field")?.querySelector(".field-error")?.textContent).toBe("Pick a time after now");
  expect(until().getAttribute("aria-invalid")).toBe("true");
  fireEvent.change(until(), { target: { value: "" } });
  fireEvent.click(drawer().getByRole("button", { name: "Pause" }));
  await advance(0);
  expect(until().closest(".field")?.querySelector(".field-error")?.textContent).toBe("Enter a date and time");
  expect(writes(api)).toEqual([]);
});

test("Resume all shows only while a line is paused and sends {paused_until: <now>} to all, with no manual", async () => {
  const paused = lines().map((row) => (row.line_id === "L04" ? { ...row, paused_until: AT_14 } : row));
  const api = controls("/water", OPERATOR, paused);
  fireEvent.click(screen.getByRole("button", { name: "Resume all" }));
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/all", { paused_until: new Date(NOW).toISOString() }]]);
  cleanup(); stopFixtureApi();
  controls("/water", OPERATOR, lines());
  expect(screen.queryByRole("button", { name: "Resume all" })).toBeNull();
  cleanup(); stopFixtureApi();
  // Fog and L03 have no Resume of their own, so what `all` left on them is still `all`'s to clear.
  for (const id of ["L03", "L11"]) {
    controls("/water", OPERATOR, lines().map((row) => (row.line_id === id ? { ...row, paused_until: AT_14 } : row)));
    expect(screen.getByRole("button", { name: "Resume all" })).toBeTruthy();
    cleanup(); stopFixtureApi();
  }
  // One that has passed is no reason to offer it.
  controls("/water", OPERATOR, lines().map((row) => (row.line_id === "L04" ? { ...row, paused_until: "2026-11-02T17:00:00.000Z" } : row)));
  expect(screen.queryByRole("button", { name: "Resume all" })).toBeNull();
});

test("Resume on a paused line sends {paused_until: <now>} with no manual; nothing while it is not paused", async () => {
  const api = startFixtureApi();
  renderWithApi(<ResumeLine line={{ line_id: "L04", paused_until: AT_14 }} />);
  fireEvent.click(screen.getByRole("button", { name: "Resume Center mister" }));
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L04", { paused_until: new Date(NOW).toISOString() }]]);
  cleanup();
  renderWithApi(<ResumeLine line={{ line_id: "L04", paused_until: null }} />);
  expect(screen.queryByRole("button")).toBeNull();
  cleanup();
  renderWithApi(<ResumeLine line={{ line_id: "L04", paused_until: AT_14 }} />, { role: "viewer" });
  expect(screen.queryByRole("button")).toBeNull();
});

test("the block menu's pause sends that line's paused_until and no manual", async () => {
  const api = startFixtureApi();
  renderWithApi(<PauseAfterRun lineId="L01" until={AT_14} />);
  fireEvent.click(screen.getByRole("button", { name: "Pause Wall drip until after this run" }));
  await advance(0);
  expect(writes(api).map((row) => [row.path, row.body])).toEqual([["/api/v1/irrigation/lines/L01", { paused_until: AT_14 }]]);
});

test("a viewer sees no Pause, Resume or drawer; with changes off they are disabled; until the lines answer there is nothing to pause", () => {
  const paused = lines().map((row) => (row.line_id === "L04" ? { ...row, paused_until: AT_14 } : row));
  controls("/water?pause=all", { role: "viewer" }, paused);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  cleanup(); stopFixtureApi();
  controls("/water", { role: "operator", writesEnabled: false }, paused);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Pause lines…" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Resume all" }).disabled).toBe(true);
  cleanup(); stopFixtureApi();
  controls("/water?pause=all", OPERATOR, null);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Pause lines…" }).disabled).toBe(true);
});

test("?pause= for L03, L11 or a stray id opens nothing", () => {
  for (const id of ["L03", "L11", "L99"]) {
    controls(`/water?pause=${id}`);
    expect(screen.queryByRole("dialog"), id).toBeNull();
    cleanup(); stopFixtureApi();
  }
});
