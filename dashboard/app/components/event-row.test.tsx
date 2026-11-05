// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// One log entry in one line (redesign §5.9): the time, who wrote it (AI for Iris and the agents only), what changed, the
// reason in quotes and the outcome pill; an Iris plan of several changes collapses to "N changes" and expands to them.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test } from "vitest";
import { catalogOf } from "../lib/activity";
import type { ActivityEntry, Setting } from "../lib/operator-api";
import { SessionProvider } from "../lib/session";
import { EventRow } from "./event-row";
import { denver, renderUi, text } from "./test-render";

afterEach(cleanup);
const CATALOG = catalogOf([{ param: "mister_engage_kpa", label: "VPD Mister Engage", kind: "numeric", unit: "kPa", step: 0.05 }] as unknown as Setting[]);
const ME = { username: "jason", display_name: "Jason" };
const entry = (row: Partial<ActivityEntry>): ActivityEntry => ({
  id: 25, ts: denver("07:41"), actor: { source: "dashboard", name: "jason" }, action: "setting.write", target: "mister_engage_kpa",
  detail: { before: 1.6, value: 1.55, manual: { before: false, after: true } }, reason: "Vandas drying", state: "confirmed", confirmed_at: denver("07:42"),
  experiment_id: null, request_id: null, ...row,
});
const row = (props: Partial<ActivityEntry> = {}) => renderUi(<ol><EventRow entry={entry(props)} catalog={CATALOG} me={ME} id="activity-25" /></ol>);

test("one line: the mono time, the writer in R4's words, the change, the reason in quotes, and the outcome pill with its time", () => {
  const { container } = row();
  const item = container.querySelector("li.event")!;
  expect(item.id).toBe("activity-25");
  const time = item.querySelector(".event-when time")!;
  expect([time.textContent, time.getAttribute("dateTime")]).toEqual(["07:41", denver("07:41")]);
  expect(time.getAttribute("title")).toContain("GMT-07:00");
  expect(text(item.querySelector(".event-by"))).toBe("Jason · dashboard");
  expect(text(item.querySelector(".act-entry-text"))).toBe("VPD Mister Engage 1.60 → 1.55 kPa · manual");
  expect(text(item.querySelector(".event-reason"))).toBe("“Vandas drying”");
  expect(text(item.querySelector(".act-entry-outcome"))).toBe("Confirmed 07:42");
  // No heading per entry: a line of a list, not a section.
  expect(screen.queryByRole("heading")).toBeNull();
  expect(item.querySelector("[data-badge=ai]")).toBeNull();
});

test("Iris and agent entries carry AI beside the name; a person's and the forecast engine's never do", () => {
  for (const [actor, ai] of [
    [{ source: "iris", name: "iris" }, true], [{ source: "agent:steward", name: "steward" }, true],
    [{ source: "system", name: "forecast-engine" }, false], [{ source: "dashboard", name: "jason" }, false],
  ] as const) {
    const { container } = row({ actor });
    expect(container.querySelector(".event-by [data-badge=ai]") !== null, actor.source).toBe(ai);
    cleanup();
  }
});

test("an Iris plan of several changes reads as one entry, \"SUNRISE plan · 3 changes\", that expands to them", () => {
  const changes = [{ ts: denver("13:00"), param: "mister_engage_kpa", value: 1.7 }, { ts: denver("16:30"), param: "mister_pulse_gap_s", value: 55 }, { ts: denver("19:00"), param: "x", value: 90 }];
  const { container } = row({ actor: { source: "iris", name: "iris" }, action: "plan.write", target: "iris-1", detail: { trigger: "SUNRISE", changes }, reason: null, state: null });
  const summary = container.querySelector(".act-plan > summary")!;
  expect(text(summary)).toBe("SUNRISE plan · 3 changes");
  fireEvent.click(summary);
  expect([...container.querySelectorAll(".plan-changes li")].map(text)).toEqual(["VPD Mister Engage 1.70 kPa from 13:00", "mister_pulse_gap_s 55 from 16:30", "x 90 from 19:00"]);
  expect(container.querySelector(".act-entry-outcome")?.textContent).toBe("");
});

test("the time is HH:MM on any day, its day the header's (the full time in its title); nothing is served with a style attribute", () => {
  const { container } = row({ ts: "2026-10-31T13:41:00Z" });
  expect(container.querySelector(".event-when time")?.textContent).toBe("07:41");
  expect(container.querySelector(".event-when time")?.getAttribute("title")).toContain("10/31/2026");
  const html = renderToStaticMarkup(<MemoryRouter><SessionProvider><ol><EventRow entry={entry({})} catalog={CATALOG} me={null} /></ol></SessionProvider></MemoryRouter>);
  expect(html).not.toMatch(/\sstyle=/);
  // Before API-01 answers, the person reads by username.
  expect(html).toContain("jason · dashboard");
});
