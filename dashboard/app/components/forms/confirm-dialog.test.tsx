// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The water confirm dialog (#1125, design system §4.4) with the C hook against #1122's fixture API: the title and the
// line under it, the fields per kind, the fixed L01 mode before the engine, one request on a double tap, and refusals.
import { useState } from "react";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { useIrrigationRead } from "../../lib/queries";
import { UUID, advance, renderWithApi, startFixtureApi, stopFixtureApi, writes } from "../../lib/test-fixture-api";
import { hasWords, words } from "../../lib/words";
import { useCommand } from "../../lib/writes";
import { Button } from "../button";
import { text } from "../test-render";
import { ConfirmDialog, lineLabel, runFields, type ConfirmDialogProps } from "./index";

afterEach(() => { cleanup(); stopFixtureApi(); });
const dialog = () => document.querySelector("dialog")!;
/** The refusal inside the dialog (the toast stack is an alert region too). */
const refusal = () => within(dialog()).getByRole("alert");
/**
 * A refusal's words as `problemMessage` gives them: the `problem_code` words once #1124's table has the code, else the
 * Problem's own detail. Either way the test holds, whichever of #1124 and #1125 lands first.
 */
const refusalWords = (code: string, detail: string) => (hasWords("problem_code", code) ? words("problem_code", code) : detail);

/** A line card's Run…: the dialog, sent through the C hook, closed when the server takes the run. */
function Run({ lineId, fixed, bounds, today }: { lineId: string; fixed?: number; bounds?: ConfirmDialogProps["bounds"]; today?: number }) {
  const status = useIrrigationRead();
  const command = useCommand({ operation: "API-IRR-06" });
  const [open, setOpen] = useState(false);
  const line = status.data?.lines.find((item) => item.line_id === lineId);
  if (!line) return <p>Loading</p>;
  const shown = today === undefined ? line : { ...line, today: { ...line.today, minutes: today } };
  return <div>
    <Button write onClick={() => setOpen(true)}>Run…</Button>
    <p data-testid="phase">{command.phase}</p>
    <ConfirmDialog open={open} onClose={() => setOpen(false)} line={shown} fixed={fixed === undefined ? null : { minutes: fixed }} bounds={bounds}
      sending={command.sending} error={command.error} onRun={async (body) => (await command.send({ line_id: lineId }, body)).ok} />
  </div>;
}
async function openRun(props: Parameters<typeof Run>[0], fixture: string | string[] = "in-band") {
  const api = startFixtureApi({ fixture });
  renderWithApi(<Run {...props} />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Run…" }));
  return api;
}
const title = () => text(screen.getByRole("heading", { level: 2 }));

test("\"Run L01 Wall drip (clean) for 10 min?\", \"Starts now · wall drip today 10 of 60 min\", Duration, Cancel and \"Run 10 min\"", async () => {
  await openRun({ lineId: "L01", today: 10 });
  expect(dialog().hasAttribute("open")).toBe(true);
  expect(title()).toBe("Run L01 Wall drip (clean) for 10 min?");
  expect(text(document.getElementById(dialog().getAttribute("aria-describedby")!))).toBe("Starts now · wall drip today 10 of 60 min");
  expect(screen.getByLabelText("Duration")).toBe(document.activeElement);
  expect(text(dialog().querySelector(".field-hint"))).toBe("Allowed 1–120 min · step 1");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "12" } });
  expect(title()).toBe("Run L01 Wall drip (clean) for 12 min?");
  expect(screen.getByRole("button", { name: "Run 12 min" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(dialog().hasAttribute("open")).toBe(false);
});

test("a double tap on Run sends one request with one Idempotency-Key and no manual; the dialog closes on the 202", async () => {
  const api = await openRun({ lineId: "L01" });
  const run = screen.getByRole("button", { name: "Run 10 min" });
  fireEvent.click(run);
  fireEvent.click(run);
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 10 } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(dialog().hasAttribute("open")).toBe(false);
  expect(screen.getByTestId("phase").textContent).toBe("waiting");
  await advance(5000);
  expect(screen.getByTestId("phase").textContent).toBe("done");
});

test("FERT in the title on a fertilizer line; drips add prewet and flush, misters take pulses; each kind sends its own fields", async () => {
  expect(runFields("drip")).toEqual(["minutes"]);
  expect(runFields("fert_drip")).toEqual(["minutes", "prewet_s", "flush_s"]);
  expect(runFields("mister")).toEqual(["on_s", "count", "gap_s"]);
  expect(runFields("fert_mister")).toEqual(["on_s", "count", "gap_s", "prewet_s", "flush_s"]);
  expect(lineLabel({ zone: "west", kind: "fert_mister", name: "Mister • West Wall (Fert.)" })).toBe("West feed");
  expect(lineLabel({ zone: "supply", kind: "master", name: "Valve • Fert. Master" })).toBe("Fert master");
  await openRun({ lineId: "L02" });
  expect(title()).toBe("Run L02 Wall feed (FERT) for 10 min?");
  expect(text(document.getElementById(dialog().getAttribute("aria-describedby")!))).toBe("Starts now · wall feed today 0 min, no cap");
  expect(text(dialog().querySelector(".dialog-note"))).toBe("Prewets on L01, feeds through L03 from the tank, then flushes");
  expect(["Duration", "Prewet on the clean line", "Flush on the clean line"].map((label) => (screen.getByLabelText(label) as HTMLInputElement).value)).toEqual(["10", "120", "240"]);
  cleanup();
  stopFixtureApi();
  const api = await openRun({ lineId: "L07" }, ["schedules-paused", "firmware-before-1343"]);
  expect(title()).toBe("Run L07 South mister (clean) for 4 × 45 s?");
  expect(screen.queryByLabelText("Duration")).toBeNull();
  fireEvent.change(screen.getByLabelText("Pulses"), { target: { value: "3" } });
  expect(screen.getByRole("button", { name: "Run 3 × 45 s" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Run 3 × 45 s" }));
  await advance(0);
  // L07's Schedule is off and the controller still runs the firmware before #1343: refused in words with the form, and
  // the dialog stays open.
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", body: { on_s: 45, count: 3, gap_s: 60 } })]);
  expect(refusal().textContent).toBe(refusalWords("not_schedule_member", "L07 takes run-now only with Schedule on until the next firmware flash"));
  expect(dialog().hasAttribute("open")).toBe(true);
});

test("before the irrigation firmware L01 runs in fixed mode: no Duration, its irrig_wall_duration_min named, and no minutes sent", async () => {
  const api = await openRun({ lineId: "L01", fixed: 12 }, "before-engine");
  expect(title()).toBe("Run L01 Wall drip (clean) for 12 min?");
  expect(screen.queryByLabelText("Duration")).toBeNull();
  expect(text(dialog().querySelector(".dialog-note"))).toBe("Runs for the Wall drip duration (irrig_wall_duration_min): 12 min");
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("button", { name: "Run 12 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", body: {} })]);
  expect(dialog().hasAttribute("open")).toBe(false);
});

test("the optional reason goes with the run only when typed, and each opening starts empty", async () => {
  const api = await openRun({ lineId: "L01" });
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "  Flush the stagnant line " } });
  fireEvent.click(screen.getByRole("button", { name: "Run 10 min" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", body: { minutes: 10, reason: "Flush the stagnant line" } })]);
  fireEvent.click(screen.getByRole("button", { name: "Run…" }));
  expect((screen.getByLabelText("Reason (optional)") as HTMLInputElement).value).toBe("");
});

test("out of bounds reads beside the field and sends nothing; the server's 422 does the same with its own bounds; busy reads its reason", async () => {
  const api = await openRun({ lineId: "L01", bounds: { minutes: { min: 1, max: 500 } } });
  fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Run 0 min" }));
  await advance(0);
  expect(text(refusal())).toBe("Must be 1–500 min");
  expect(screen.getByLabelText("Duration").getAttribute("aria-invalid")).toBe("true");
  expect(writes(api)).toEqual([]);
  fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "200" } });
  fireEvent.click(screen.getByRole("button", { name: "Run 200 min" }));
  await advance(0);
  expect(writes(api)).toHaveLength(1);
  expect(text(refusal())).toBe("Must be 1–120 min");
  expect(screen.getByLabelText("Duration").getAttribute("aria-describedby")).toContain(refusal().id);
  expect(dialog().hasAttribute("open")).toBe(true);
  cleanup();
  stopFixtureApi();
  const busy = await openRun({ lineId: "L01" }, "l01-running");
  fireEvent.click(screen.getByRole("button", { name: "Run 10 min" }));
  await advance(0);
  expect(writes(busy)).toHaveLength(1);
  expect(refusal().textContent).toBe(refusalWords("line_busy", "L01 is already running"));
  expect(dialog().hasAttribute("open")).toBe(true);
});

test("Run is a write control: disabled while changes are switched off", async () => {
  startFixtureApi({ fixture: "writes-off" });
  renderWithApi(<Run lineId="L01" />, { role: "operator", writesEnabled: false });
  await advance(0);
  expect(screen.getByRole("button", { name: "Run…" }).hasAttribute("disabled")).toBe(true);
});
