// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import type { CalendarDay, MetricResult } from "../../lib/operator-api";
import { ciDomain, confirmedShare, estimateWords, filledIds, historyDays, historySentence, historyTotals, plannerHref, runActions, selectedExperiment, triggerMix, verdict } from "../../lib/planner";
import { problemMessage } from "../../lib/writes";
import { renderUi } from "../test-render";
import { ArmCalendar, cellWords } from "./arm-calendar";
import { CiRow } from "./ci-row";
import { RunControls } from "./study-run";

afterEach(cleanup);

const metric = (change: Partial<MetricResult>): MetricResult => ({
  id: "night_vpd_in_band_pct", label: "Night VPD in band", unit: "pts", role: "primary", direction: "higher", estimate: 9, ci_low: 2, ci_high: 16, n_pairs: 15, ...change,
});

test("an interval says better or worse only when it leaves zero on that side", () => {
  expect(verdict(metric({}))).toBe("better");
  expect(verdict(metric({ estimate: -9, ci_low: -16, ci_high: -2 }))).toBe("worse");
  expect(verdict(metric({ estimate: 1, ci_low: -3, ci_high: 5 }))).toBe("no_difference");
  // Lower is better: an interval below zero is better.
  expect(verdict(metric({ direction: "lower", estimate: -4, ci_low: -6, ci_high: -1 }))).toBe("better");
  expect(verdict(metric({ ci_low: null }))).toBe("not_computed");
});

test("a guardrail is judged against its margin, and heat is reported, never scored", () => {
  const guard = (low: number, high: number) => verdict(metric({ role: "guardrail", estimate: (low + high) / 2, ci_low: low, ci_high: high }), 2);
  expect(guard(-1.5, 1)).toBe("not_worse");
  expect(guard(-5, -2.5)).toBe("worse");
  expect(guard(-3, 1)).toBe("inconclusive");
  expect(verdict(metric({ id: "heat1_hours", role: "secondary", direction: "report", estimate: 4, ci_low: 2, ci_high: 6 }))).toBe("reported");
});

test("words and axis come from the analyzer's numbers only", () => {
  expect(estimateWords(metric({}))).toBe("+9.0 pts (95% CI 2.0 to 16.0)");
  expect(estimateWords(metric({ estimate: null }))).toBe("Not computed");
  expect(ciDomain(metric({}))).toBeCloseTo(16 * 1.15);
  expect(ciDomain(metric({ estimate: null, ci_low: null, ci_high: null }))).toBe(1);
});

test("the CI row draws only a computed interval and says the verdict in words", () => {
  const { container } = render(<ul><CiRow metric={metric({ role: "guardrail", estimate: -0.5, ci_low: -1.5, ci_high: 0.5 })} margin={2} /></ul>);
  expect(container.querySelector("li")?.getAttribute("data-verdict")).toBe("not_worse");
  expect(container.querySelector(".pln-ci-margin")).not.toBeNull();
  expect(container.querySelector(".pln-ci-dot")).not.toBeNull();
  expect(screen.getByText("Not worse (within the margin)")).toBeTruthy();
  cleanup();
  const bare = render(<ul><CiRow metric={metric({ estimate: null, ci_low: null, ci_high: null })} /></ul>);
  expect(bare.container.querySelector(".pln-ci-dot")).toBeNull();
  expect(bare.container.querySelector(".pln-ci-margin")).toBeNull();
});

const day = (date: string, change: Partial<CalendarDay> = {}): CalendarDay => ({
  date, pair_index: 0, arm: "ai", status: "locked", starts_at: null, ends_at: null, deviations: 0, events: [], ...change,
});

test("each calendar cell says its arm, pair, deviations and when in words", () => {
  expect(cellWords(day("2026-12-01", { deviations: 1, events: ["override"] }), "2026-12-01")).toBe("Dec 1: AI planner, pair 1, 1 deviation (override), today");
  expect(cellWords(day("2026-12-02", { arm: "baseline" }), "2026-12-01")).toBe("Dec 2: Frozen baseline, pair 1, to come");
  const { container } = render(<ArmCalendar days={[day("2026-12-01", { deviations: 2 }), day("2026-12-02", { arm: "baseline" })]} today="2026-12-01" nextBoundary={null} />);
  const cells = container.querySelectorAll("li.pln-day");
  expect(cells).toHaveLength(2);
  expect(cells[0].getAttribute("aria-current")).toBe("date");
  expect(cells[0].hasAttribute("data-deviation")).toBe(true);
  expect(cells[1].getAttribute("data-when")).toBe("future");
  cleanup();
  expect(render(<ArmCalendar days={[]} today="2026-12-01" nextBoundary={null} />).container.textContent).toBe("No study day is assigned yet.");
});

test("a run's days say void, cancelled and the boundary result in words; a confirmed boundary says nothing", () => {
  expect(cellWords(day("2026-12-01", { status: "done", events: ["boundary_confirmed"] }), "2026-12-05")).toBe("Dec 1: AI planner, pair 1");
  expect(cellWords(day("2026-12-01", { status: "done", deviations: 1, events: ["boundary_unconfirmed"] }), "2026-12-05"))
    .toBe("Dec 1: AI planner, pair 1, 1 deviation (boundary not confirmed)");
  expect(cellWords(day("2026-12-02", { status: "void", events: ["paused"] }), "2026-12-05")).toBe("Dec 2: AI planner, pair 1, void, runs again (paused)");
  expect(cellWords(day("2026-12-09", { status: "cancelled" }), "2026-12-05")).toBe("Dec 9: AI planner, pair 1, cancelled, to come");
  const { container } = render(<ArmCalendar days={[day("2026-12-02", { status: "void", events: ["stopped"] })]} today="2026-12-05" nextBoundary={null} />);
  expect(container.querySelector("li.pln-day")?.getAttribute("data-status")).toBe("void");
  expect(container.textContent).toContain("struck through");
});

test("the controls follow the run's status", () => {
  expect(runActions(null)).toEqual(["start"]);
  expect(runActions("scheduled")).toEqual(["pause", "stop"]);
  expect(runActions("running")).toEqual(["pause", "stop"]);
  expect(runActions("paused")).toEqual(["resume", "stop"]);
  expect(runActions("stopped")).toEqual(["start"]);
  expect(runActions("finished")).toEqual(["start"]);
  expect(filledIds(["a", "b", "c"], [{ a: null, b: 2 }, { c: null, d: 1 }])).toEqual(["b", "d"]);
});

test("Start is disabled with why until the definition is pre-registered; a viewer sees no control", () => {
  const operator = renderUi(<RunControls status={null} preregistered={false} onChoose={() => {}} />, { role: "operator", writesEnabled: true });
  const start = operator.getByRole("button", { name: "Start the study" }) as HTMLButtonElement;
  expect(start.disabled).toBe(true);
  expect(document.getElementById(start.getAttribute("aria-describedby") ?? "")?.textContent).toContain("pre-registered");
  cleanup();
  const viewer = renderUi(<RunControls status="paused" preregistered onChoose={() => {}} />, { role: "viewer", writesEnabled: true });
  expect(viewer.queryAllByRole("button")).toHaveLength(0);
  cleanup();
  const paused = renderUi(<RunControls status="paused" preregistered onChoose={() => {}} />, { role: "operator", writesEnabled: true });
  expect(paused.getAllByRole("button").map((button) => button.textContent)).toEqual(["Resume the study", "Stop the study"]);
});

test("a study_state refusal reads as the server's own sentence", () => {
  const refusal = { type: "about:blank", title: "Refused", status: 422, code: "study_state", retryable: false, min: null, max: null, current: null, retry_after_s: null };
  expect(problemMessage({ ...refusal, detail: "mister_engage_kpa is held manual by jason; release it first." })).toBe("mister_engage_kpa is held manual by jason; release it first.");
  expect(problemMessage(refusal)).toBe("The study cannot do that now");
});

test("history: window, redirect, totals and words", () => {
  expect(historyDays(new URLSearchParams("days=90"))).toBe(90);
  expect(historyDays(new URLSearchParams("days=7"))).toBe(30);
  expect(plannerHref("https://dashboard.verdify.ai/planner?x=1")).toBe("/plan?x=1");
  expect(triggerMix({ DEVIATION: 2, SUNSET: 1, MIDNIGHT: 1 })).toBe("MIDNIGHT, SUNSET, DEVIATION ×2");
  expect(confirmedShare({ changes_dispatched: 4, changes_confirmed: 3 })).toBe("75%");
  expect(confirmedShare({ changes_dispatched: 0, changes_confirmed: 0 })).toBe("—");
  const base = { plans_written: 3, triggers: {}, params_dispatched: 0, changes_dispatched: 0, changes_confirmed: 0, required_due: 3, required_written: 3, required_failed: 0, required_late: 0, cost: null };
  const totals = historyTotals([{ ...base, date: "2026-11-04" }, { ...base, date: "2026-11-05", required_written: 2, required_failed: 1, changes_dispatched: 5 }]);
  expect(historySentence(totals)).toBe("5 of 6 required plans written · 1 failed · 6 plans in all · changes reached the controller on 1 of 2 days");
});

test("the page opens ?id=, else a running study, else the first listed", () => {
  const item = (id: string, status: "defined" | "running" | "closed") => ({ id, experiment_id: null, study_id: id, name: id, question: null, status, raw_status: null, arms: [], started_at: null, ended_at: null, updated_at: null, days_planned: 60, days_done: 0, deviations: 0, closed_at: null, closed_reason: null });
  const items = [item("a", "defined"), item("b", "running"), item("c", "closed")];
  expect(selectedExperiment(items, null)?.id).toBe("b");
  expect(selectedExperiment(items, "c")?.id).toBe("c");
  expect(selectedExperiment(items, "nope")).toBeNull();
  expect(selectedExperiment([], null)).toBeNull();
});
