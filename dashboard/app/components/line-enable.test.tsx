// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The line enable switch (#1182) against #1122's fixture API, at 10:42 on Monday 2026-11-02.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { useLocation } from "react-router";
import { afterEach, expect, test } from "vitest";
import { fixture } from "../../e2e/fixtures/index.mjs";
import type { IrrigationLine, IrrigationStatus } from "../lib/operator-api";
import type { Session } from "../lib/session";
import { NOW, OPERATOR, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../lib/test-fixture-api";
import { DISABLE_HINT, LineEnable } from "./line-enable";

afterEach(() => { cleanup(); stopFixtureApi(); });

const lineOf = (names: string | string[], id: string) => (fixture(names, { now: NOW })["API-IRR-01"] as IrrigationStatus).lines.find((line) => line.line_id === id)!;
const Where = () => <span data-testid="where">{useLocation().search}</span>;
const where = () => screen.getByTestId("where").textContent;
function mount(line: IrrigationLine, session: Partial<Session> = OPERATOR, names: string | string[] = "lines-unchecked") {
  const api = startFixtureApi({ fixture: names });
  renderWithApi(<><LineEnable line={line} /><Where /></>, session);
  return { api, control: () => screen.queryByRole("switch", { name: `Enabled ${line.line_id}` }) };
}
const checked = { checked_by: "jason", checked_at: "2026-11-01T15:00:00Z", note: null };

test("switching off sends {enabled: false, manual: true} at once, one key, and says what off stops", async () => {
  const { api, control } = mount(lineOf("lines-unchecked", "L04"));
  expect(control()?.getAttribute("aria-checked")).toBe("true");
  expect(screen.getByText(DISABLE_HINT)).toBeTruthy();
  expect(DISABLE_HINT).toBe("Off stops all water on this line, VPD misting included");
  expect(control()?.getAttribute("aria-describedby")).toBe(screen.getByText(DISABLE_HINT).id);
  fireEvent.click(control()!);
  expect(control()?.textContent).toContain("Sending…");
  await advance(0);
  expect(writes(api).map((row) => [row.method, row.path, row.body])).toEqual([["PUT", "/api/v1/irrigation/lines/L04", { enabled: false, manual: true }]]);
  expect(where()).toBe("");
});

test("switching on a line with its check sends {enabled: true, manual: true} at once; with no check it sends nothing and opens the check sheet", async () => {
  const first = mount({ ...lineOf("lines-unchecked", "L02"), check: checked });
  fireEvent.click(first.control()!);
  await advance(0);
  expect(writes(first.api).map((row) => row.body)).toEqual([{ enabled: true, manual: true }]);
  expect(where()).toBe("");
  cleanup(); stopFixtureApi();
  const second = mount(lineOf("lines-unchecked", "L02"));
  fireEvent.click(second.control()!);
  await advance(0);
  expect(writes(second.api)).toEqual([]);
  expect(where()).toBe("?check=L02");
});

test("a refusal reads in words beside the switch; a viewer sees none of it; with changes off it is disabled", async () => {
  const refused = mount({ ...lineOf("lines-unchecked", "L02"), check: null, enabled: true });
  refused.api.respond({ method: "POST", url: "/__control", body: { fixture: "writes-off" } });
  fireEvent.click(refused.control()!);
  await advance(0);
  const error = document.querySelector(".field-error")!;
  expect(error.textContent).toBe("Changes are switched off");
  expect(refused.control()?.getAttribute("aria-describedby")).toContain(error.id);
  cleanup(); stopFixtureApi();
  const viewer = mount(lineOf("lines-unchecked", "L02"), { role: "viewer" });
  expect(viewer.control()).toBeNull();
  expect(screen.queryByText(DISABLE_HINT)).toBeNull();
  cleanup(); stopFixtureApi();
  const off = mount(lineOf("lines-unchecked", "L02"), { role: "operator", writesEnabled: false });
  expect(off.control()?.hasAttribute("disabled")).toBe(true);
  expect(screen.getByText(DISABLE_HINT)).toBeTruthy();
});
