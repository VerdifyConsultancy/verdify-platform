// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * Planner → Experiments' study run (#1360) against #1122's fixture API in process: the Run panel's facts, the operator's
 * controls for each status, the confirmation and its API-28 write (one key, the first day), a viewer with no controls,
 * the per-arm and cost panels and the daily outcomes' live columns. The clock is 10:42 America/Denver on 2026-11-02.
 */
import { act, cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { STUDY_ID } from "../../e2e/fixtures/operations.mjs";
import { text } from "../components/test-render";
import { OPERATOR, UUID, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, type FixtureOptions } from "../lib/test-fixture-api";
import type { Session } from "../lib/session";
import PlannerExperimentsPage from "./planner-experiments";

afterEach(() => { cleanup(); stopFixtureApi(); });

async function load(options: FixtureOptions = {}, session: Partial<Session> = OPERATOR) {
  const api = startFixtureApi(options);
  const view = renderWithApi(<PlannerExperimentsPage />, session, "/planner/experiments");
  await advance(1);
  await advance(1);
  return { api, view };
}
const runPanel = () => screen.getByRole("heading", { name: "Run" }).closest("section") as HTMLElement;
const buttons = (root: HTMLElement) => within(root).queryAllByRole("button").map((button) => button.textContent);

test("a defined study not yet started offers Start; the dialog sends API-28 once with tomorrow as the first day", async () => {
  const { api } = await load();
  expect(text(runPanel())).toContain("Not started.");
  expect(buttons(runPanel())).toEqual(["Start the study"]);
  fireEvent.click(within(runPanel()).getByRole("button", { name: "Start the study" }));
  const dialog = screen.getByRole("dialog", { name: "Start the study?" });
  expect((within(dialog).getByLabelText("First study day") as HTMLInputElement).value).toBe("2026-11-03");
  fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), { target: { value: "Hermes against the baseline" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
  await advance(1);
  await advance(1);
  const sent = requests(api, "API-28");
  expect(sent).toHaveLength(1);
  expect(sent[0].path).toBe(`/api/v1/planner/experiments/${STUDY_ID}/start`);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(sent[0].body).toEqual({ first_day: "2026-11-03", reason: "Hermes against the baseline" });
  // The refetched study reads scheduled, with Pause and Stop.
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(buttons(runPanel())).toEqual(["Pause the study", "Stop the study"]);
  expect(text(runPanel())).toContain("Scheduled");
});

test("a running study offers Pause and Stop, shows the run's facts, and Stop's reason goes with it", async () => {
  const { api } = await load({ fixture: "planner-study-interim" });
  const panel = runPanel();
  expect(buttons(panel)).toEqual(["Pause the study", "Stop the study"]);
  const facts = text(panel);
  expect(facts).toContain("Hermes");
  expect(facts).toContain("18 done, 12 left of 30");
  expect(facts).toContain("of 48 planner settings held at the baseline");
  expect(facts).toContain("Seed 1360");
  fireEvent.click(within(panel).getByRole("button", { name: "Stop the study" }));
  const dialog = screen.getByRole("dialog", { name: "Stop the study?" });
  expect(within(dialog).queryByLabelText("First study day")).toBeNull();
  fireEvent.change(within(dialog).getByLabelText("Reason (optional)"), { target: { value: "Cold snap" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Stop" }));
  await advance(1);
  await advance(1);
  expect(requests(api, "API-28").map((row) => [row.path, row.body])).toEqual([[`/api/v1/planner/experiments/${STUDY_ID}/stop`, { reason: "Cold snap" }]]);
  expect(buttons(runPanel())).toEqual(["Start the study"]);
});

test("Cancel sends nothing, and a refusal shows the server's own sentence in the dialog", async () => {
  const { api } = await load({ fixture: "planner-study-interim" });
  fireEvent.click(within(runPanel()).getByRole("button", { name: "Pause the study" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  // A second tab paused the run meanwhile: this Pause is refused with study_state, in the server's words.
  await act(async () => { api.respond({ method: "POST", url: `/v1/planner/experiments/${STUDY_ID}/pause`, headers: {}, body: "{}" }); });
  fireEvent.click(within(runPanel()).getByRole("button", { name: "Pause the study" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Pause" }));
  await advance(1);
  expect(within(screen.getByRole("dialog")).getByRole("alert").textContent).toBe("The run is already paused.");
});

test("a viewer sees the run but no control", async () => {
  await load({ fixture: "planner-study-interim" }, { role: "viewer", writesEnabled: true });
  expect(text(runPanel())).toContain("Hermes");
  expect(buttons(runPanel())).toEqual([]);
  expect(document.querySelector("[data-write-control]")).toBeNull();
});

test("the per-arm, cost and daily outcomes panels show the live values and keep the Cortex cohorts apart", async () => {
  await load({ fixture: "planner-study-interim" });
  const section = (name: string) => screen.getByRole("heading", { name }).closest("section") as HTMLElement;
  expect(text(section("Per arm"))).toContain("Live values, not the frozen analysis");
  expect(text(section("Per arm"))).toContain("Electric heat minutes");
  expect(text(section("Per arm"))).not.toContain("Night VPD in band");
  const cost = text(section("Cost"));
  expect(cost).toContain("Hermes records no per-call tokens");
  expect(cost).toContain("cortex-pre-1482");
  expect(cost).toContain("cortex-1482");
  const headers = within(section("Daily outcomes")).getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(headers).toContain("Heater minutes (heat1 + heat2)");
  expect(headers).toContain("Required plans written");
  expect(headers).not.toContain("Night VPD in band");
});
