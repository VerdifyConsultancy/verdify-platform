// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { STATE_REASON_CODES, type StateReason, type StateReasonCode } from "../lib/vocab";
import { StateReasonLine, reasonParts, reasonShows } from "./state-reason-line";
import { NBSP, denver, renderUi, text } from "./test-render";

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date(denver("06:10"))); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const SINCE = denver("06:02");
const DETAIL: Partial<Record<StateReasonCode, string>> = { clamped: "VPD-high moisture guardrail", experiment: "e-vanda-night", push_failed: "timeout" };
// The words each code reads, with `since` at 06:02 and with `since` null (design system §4.2; VD-03).
const WITH_SINCE: Record<StateReasonCode, string> = {
  clamped: "Clamped to 1.15 kPa: VPD-high moisture guardrail",
  experiment: "Held by experiment e-vanda-night: applies when it ends",
  deferred: "Sent 06:02; waiting: controller heap low",
  push_failed: "Sent 06:02; push failed: timeout",
  writer_down: "Ingestor not running since 06:02",
  controller_not_reporting: "Controller not reporting since 06:02",
  no_engine: "Applies with the irrigation firmware",
  no_match: "Sent 06:02; no matching readback yet",
};
const WITHOUT_SINCE: Record<StateReasonCode, string> = {
  ...WITH_SINCE,
  deferred: "Waiting: controller heap low",
  push_failed: "Push failed: timeout",
  writer_down: "Ingestor not running",
  controller_not_reporting: "Controller not reporting",
  no_match: "No matching readback yet",
};
const reason = (code: StateReasonCode, since: string | null): StateReason => ({ code, since, detail: DETAIL[code] ?? null, applied_value: code === "clamped" ? 1.15 : null });
// A request two minutes old, so a REQUESTED line shows.
const OLD_REQUEST = denver("06:08");
const line = (props: Parameters<typeof StateReasonLine>[0]) => renderUi(<StateReasonLine unit="kPa" requestedAt={OLD_REQUEST} {...props} />).container;

test("under REQUESTED, NOT CONFIRMED and HELD every StateReason code reads in words, never as its raw code", () => {
  expect(Object.keys(WITH_SINCE).sort()).toEqual([...STATE_REASON_CODES].sort());
  for (const state of ["requested", "not_confirmed", "held"]) {
    for (const code of STATE_REASON_CODES) {
      for (const [since, words] of [[SINCE, WITH_SINCE[code]], [null, WITHOUT_SINCE[code]]] as const) {
        const container = line({ state, reason: reason(code, since) });
        expect(text(container), `${state} ${code} ${since}`).toBe(words);
        for (const raw of STATE_REASON_CODES.filter((item) => item.includes("_"))) expect(container.textContent).not.toContain(raw);
        cleanup();
      }
    }
  }
});

test("no_match reads \"Sent 06:02; no matching readback yet\", and with since null or no state_reason \"No matching readback yet\"", () => {
  expect(text(line({ state: "not_confirmed", reason: { code: "no_match", since: SINCE } }))).toBe("Sent 06:02; no matching readback yet");
  cleanup();
  expect(text(line({ state: "not_confirmed", reason: { code: "no_match", since: null } }))).toBe("No matching readback yet");
  cleanup();
  expect(text(line({ state: "held", reason: null }))).toBe("No matching readback yet");
  cleanup();
  // A code this build has no words for still never shows raw.
  const unknown = line({ state: "not_confirmed", reason: { code: "brownout" as StateReasonCode, since: SINCE } });
  expect(text(unknown)).toBe("No matching readback yet");
  expect(unknown.textContent).not.toContain("brownout");
});

test("every time shown is state_reason.since as a <time>, never the request time", () => {
  const container = line({ state: "not_confirmed", reason: { code: "deferred", since: SINCE }, requestedAt: denver("05:40") });
  const time = container.querySelector("time")!;
  expect(time.getAttribute("dateTime")).toBe(SINCE);
  expect(time.textContent).toBe("06:02");
  expect(time.getAttribute("title")).toContain("GMT-07:00");
  expect(container.textContent).not.toContain("05:40");
});

test("under REQUESTED the line waits until the request is 60 s old, except no_engine, which shows at once", async () => {
  const fresh = denver("06:09", 30);
  const { container } = renderUi(<StateReasonLine state="requested" reason={{ code: "no_match", since: fresh }} requestedAt={fresh} />);
  expect(container.textContent).toBe("");
  await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
  expect(text(container)).toBe("Sent 06:09; no matching readback yet");
  cleanup();
  expect(text(renderUi(<StateReasonLine state="requested" reason={{ code: "no_engine", since: denver("06:10") }} requestedAt={denver("06:10")} />).container)).toBe("Applies with the irrigation firmware");
  cleanup();
  // Without the request time the 60 s cannot be known, so the line stays away.
  expect(renderUi(<StateReasonLine state="requested" reason={{ code: "no_match", since: SINCE }} />).container.textContent).toBe("");
});

test("CONFIRMED, NO READBACK, released and AI rows carry no reason line", () => {
  for (const state of ["confirmed", "no_readback", "released", "overridden"]) {
    expect(line({ state, reason: { code: "no_match", since: SINCE } }).textContent, state).toBe("");
    cleanup();
  }
});

test("reasonShows and reasonParts follow the same rules without rendering", () => {
  const now = Date.parse(denver("06:10"));
  expect(reasonShows("requested", null, denver("06:09", 1), now)).toBe(false);
  expect(reasonShows("requested", null, denver("06:09", 0), now)).toBe(true);
  expect(reasonShows("requested", { code: "no_engine" }, denver("06:10"), now)).toBe(true);
  expect(reasonShows("held", null, null, now)).toBe(true);
  expect(reasonShows("confirmed", { code: "no_match" }, denver("06:00"), now)).toBe(false);
  // A clamp without its unit keeps the applied value's digits; a clamp without an applied value or detail says only that.
  expect(reasonParts({ code: "clamped", applied_value: 1.15, detail: null })).toEqual(["Clamped to 1.15"]);
  expect(reasonParts({ code: "clamped", applied_value: -0.4, detail: null }, "°F")).toEqual([`Clamped to −0.4${NBSP}°F`]);
  expect(reasonParts({ code: "clamped" })).toEqual(["Clamped"]);
  expect(reasonParts({ code: "experiment", detail: "  " })).toEqual(["Held by an experiment: applies when it ends"]);
  // An unparseable since drops out like a null one.
  expect(reasonParts({ code: "writer_down", since: "not a time" })).toEqual(["Ingestor not running"]);
});
