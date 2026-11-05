// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Run… on a line card (#1136) against #1122's fixture API in process: what each kind offers and sends, the one dialog,
// refusals in words that keep it open and send nothing else, a double tap, L01 before the irrigation firmware, the readback
// and who sees it. The clock is 10:42 America/Denver on 2026-11-02.
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { IrrigationLine } from "../lib/operator-api";
import { useIrrigationRead } from "../lib/queries";
import { OPERATOR, UUID, advance, renderWithApi, requests, startFixtureApi, stopFixtureApi, writes, type FixtureOptions } from "../lib/test-fixture-api";
import { words } from "../lib/words";
import { LINE_OFF_TEXT, RUN_ARRIVES_TEXT, RunControl, WaterRuns, runOffer } from "./run-dialog";
import { text } from "./test-render";

afterEach(() => { cleanup(); stopFixtureApi(); });

/** One line's Run control over the shared API-IRR-01 read, and its state as the card would show it, under the Water layout's holder. */
function Card({ lineId }: { lineId: string }) {
  const status = useIrrigationRead();
  const line = status.data?.lines.find((item) => item.line_id === lineId);
  if (!status.data || !line) return <p>Loading</p>;
  return <WaterRuns status={status.data}><div><p data-testid="state">{line.state.code}</p><RunControl line={line} engine={status.data.engine} /></div></WaterRuns>;
}
async function open(lineId: string, options: FixtureOptions = {}, session = OPERATOR) {
  const api = startFixtureApi(options);
  renderWithApi(<Card lineId={lineId} />, session);
  await advance(0);
  return api;
}
/** Moves the clock; a read's answer reaches the page on the tick after it (TanStack batches its notifications on a timer). */
const readBack = async (ms: number) => { await advance(ms); await advance(1); };
const dialog = () => document.querySelector("dialog");
const opened = () => dialog()?.hasAttribute("open") ?? false;
const title = () => text(within(dialog()!).getByRole("heading", { level: 2 }));
const facts = () => text(document.getElementById(dialog()!.getAttribute("aria-describedby")!));
const notes = () => [...dialog()!.querySelectorAll(".dialog-note")].map((node) => text(node));
const refusal = () => text(within(dialog()!).getByRole("alert"));
const press = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const fieldNames = () => [...dialog()!.querySelectorAll(".dialog-fields label")].map((node) => text(node));
type Offered = Pick<IrrigationLine, "line_id" | "kind" | "enabled" | "reserved">;
const line = (line_id: string, kind: string, enabled: boolean, reserved = false): Offered => ({ line_id, kind, enabled, reserved });

test("what a line offers (#1343): every enabled wet line runs once, Mist now… with Schedule on or off; a disabled line enables first; before the firmware only L01", () => {
  expect(runOffer(line("L01", "drip", true), true)).toBe("run");
  expect(runOffer(line("L02", "fert_drip", true), true)).toBe("run");
  expect(runOffer(line("L08", "fert_mister", true), true)).toBe("run");
  expect(runOffer(line("L04", "mister", true), true)).toBe("mist");
  expect(runOffer(line("L07", "mister", true), true)).toBe("mist");
  // A disabled line keeps its button, which enables it first; a reserved line (no valve wired) offers nothing while off.
  for (const off of [line("L02", "fert_drip", false), line("L07", "mister", false), line("L10", "fert_mister", false)]) expect(runOffer(off, true)).toBe("enable");
  for (const reserved of [line("L05", "drip", false, true), line("L06", "fert_drip", false, true)]) expect(runOffer(reserved, true)).toBeNull();
  expect(runOffer(line("L05", "drip", true, true), true)).toBe("run");
  // L11 fog runs once with the engine, enabled or not (#1343 follow-up); before it, nothing. L03 has no card controls.
  expect(runOffer(line("L11", "fog", true), true)).toBe("run");
  expect(runOffer(line("L11", "fog", false), true)).toBe("run");
  expect(runOffer(line("L11", "fog", true), false)).toBeNull();
  expect(runOffer(line("L03", "master", true), true)).toBeNull();
  expect(runOffer(line("L01", "drip", true), false)).toBe("run");
  expect(runOffer(line("L01", "drip", false), false)).toBeNull();
  for (const other of [line("L02", "fert_drip", true), line("L04", "mister", true, true), line("L05", "drip", false)]) expect(runOffer(other, false)).toBe("arrives");
  expect(runOffer(line("L11", "fog", true), false)).toBeNull();
});

test("L01: the head, Duration 1–120 min, {minutes}; a double tap sends one Idempotency-Key, and the relay edge reads RUNNING", async () => {
  const api = await open("L01");
  expect(dialog()).toBeNull();
  press("Run… L01");
  expect(opened()).toBe(true);
  expect(title()).toBe("Run L01 Wall drip (clean) for 10 min?");
  expect(facts()).toBe("Starts now · wall drip today 0 of 60 min");
  expect(fieldNames()).toEqual(["Duration"]);
  expect(text(dialog()!.querySelector(".field-hint"))).toBe("Allowed 1–120 min · step 1");
  const run = screen.getByRole("button", { name: "Run 10 min" });
  fireEvent.click(run);
  expect(text(run)).toBe("Sending…");
  fireEvent.click(run);
  await advance(0);
  const sent = writes(api);
  expect(sent).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 10 } })]);
  expect(sent[0].idempotency_key).toMatch(UUID);
  expect(dialog()).toBeNull();
  expect(text(document.querySelector("[data-run-note]"))).toBe("Waiting for the valve to open");
  // The readback is API-IRR-01 every 5 s.
  await readBack(5000);
  expect(screen.getByTestId("state").textContent).toBe("running");
  expect(document.querySelector("[data-run-note]")).toBeNull();
  expect(writes(api)).toHaveLength(1);
});

test("L02, FERT: the title, what a feed does, no cap, and {minutes, prewet_s, flush_s}", async () => {
  const api = await open("L02", { fixture: "run-dialogs" });
  press("Run… L02");
  expect(title()).toBe("Run L02 Wall feed (FERT) for 10 min?");
  expect(facts()).toBe("Starts now · wall feed today 0 min, no cap");
  expect(notes()).toEqual(["Prewets on L01, feeds through L03 from the tank, then flushes"]);
  expect(fieldNames()).toEqual(["Duration", "Prewet on the clean line", "Flush on the clean line"]);
  fireEvent.change(screen.getByLabelText("Duration"), { target: { value: "5" } });
  press("Run 5 min");
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L02/run", body: { minutes: 5, prewet_s: 120, flush_s: 240 } })]);
});

test("L08, FERT: the mister fields plus prewet and flush, and no minutes", async () => {
  const api = await open("L08", { fixture: "run-dialogs" });
  press("Run… L08");
  expect(title()).toBe("Run L08 South feed (FERT) for 4 × 45 s?");
  expect(notes()).toEqual(["Prewets on L07, feeds through L03 from the tank, then flushes"]);
  expect(fieldNames()).toEqual(["Pulse", "Pulses", "Gap between pulses", "Prewet on the clean line", "Flush on the clean line"]);
  expect(screen.queryByLabelText("Duration")).toBeNull();
  press("Run 4 × 45 s");
  await advance(0);
  const [sent] = writes(api);
  expect(sent).toMatchObject({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L08/run" });
  expect(sent.body).toEqual({ on_s: 45, count: 4, gap_s: 60, prewet_s: 120, flush_s: 240 });
});

test("L04: Mist now… shows with Schedule off too (#1343) and sends {on_s, count, gap_s}; the cap shows, and a clean line has no feed words", async () => {
  const off = await open("L04");
  press("Mist now… L04");
  press(/^Run \d/);
  await advance(0);
  expect(writes(off)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L04/run" })]);
  cleanup(); stopFixtureApi();
  const api = await open("L04", { fixture: "run-dialogs" });
  press("Mist now… L04");
  expect(title()).toBe("Run L04 Center mister (clean) for 4 × 45 s?");
  expect(facts()).toBe("Starts now · center mister today 14 of 120 min");
  expect(notes()).toEqual([]);
  fireEvent.change(screen.getByLabelText("Pulses"), { target: { value: "2" } });
  press("Run 2 × 45 s");
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L04/run", body: { on_s: 45, count: 2, gap_s: 60 } })]);
});

test("a disabled line keeps Run… (#1343), saying it is off, and opens the check sheet for the run; L05 and L06 offer nothing while reserved", async () => {
  for (const id of ["L05", "L06"]) {
    await open(id);
    expect(screen.getByTestId("state").textContent).toBe("disabled");
    expect(screen.queryByRole("button")).toBeNull();
    cleanup(); stopFixtureApi();
  }
  for (const [id, label] of [["L02", "Run…"], ["L07", "Mist now…"]]) {
    const api = await open(id, { fixture: "lines-unchecked" });
    expect(document.querySelector(`[data-run-note="${id}"]`)?.textContent).toBe(LINE_OFF_TEXT);
    press(`${label} ${id}`);
    await advance(0);
    // Nothing is sent and no run dialog opens: the check sheet (?check=<line>&for=run) takes it from here.
    expect(dialog()).toBeNull();
    expect(writes(api)).toEqual([]);
    cleanup(); stopFixtureApi();
  }
});

test("manifold_busy keeps the dialog open with the holder and until when, changes nothing and sends nothing else", async () => {
  const api = await open("L02", { fixture: "manifold-busy" });
  press("Run… L02");
  press("Run 10 min");
  await advance(0);
  expect(opened()).toBe(true);
  expect(refusal()).toBe("L01 holds the drip manifold until 10:49; stop it or try then");
  expect(writes(api)).toHaveLength(1);
  expect(screen.getByTestId("state").textContent).toBe("idle");
  // Nothing queues: no readback runs, and the line stays as it was.
  await advance(35_000);
  expect(writes(api)).toHaveLength(1);
  expect(screen.getByTestId("state").textContent).toBe("idle");
  expect(document.querySelector("[data-run-note]")).toBeNull();
  // A new opening starts with no refusal.
  press("Cancel");
  expect(dialog()).toBeNull();
  press("Run… L02");
  expect(within(dialog()!).queryByRole("alert")).toBeNull();
});

test("each refusal reads its code in words from the words table, and the dialog stays open", async () => {
  const refused = async (lineId: string, code: string, fixture: string | string[], before: (api: ReturnType<typeof startFixtureApi>) => void = () => {}, duration?: string) => {
    const api = await open(lineId, { fixture });
    press(new RegExp(`^(Run…|Mist now…) ${lineId}$`));
    // The race the API's refusal covers: the line changed after the card last read it.
    before(api);
    if (duration) fireEvent.change(screen.getByLabelText("Duration"), { target: { value: duration } });
    press(/^Run \d/);
    await advance(0);
    expect(opened(), code).toBe(true);
    expect(refusal(), code).toBe(words("problem_code", code));
    expect(writes(api).filter((row) => row.operationId === "API-IRR-06"), code).toHaveLength(1);
    cleanup(); stopFixtureApi();
  };
  const update = (lineId: string, body: unknown) => (api: ReturnType<typeof startFixtureApi>) => {
    api.respond({ method: "PUT", url: `/api/v1/irrigation/lines/${lineId}`, headers: {}, body });
  };
  await refused("L01", "line_busy", "l01-running");
  await refused("L01", "cap_reached", "in-band", undefined, "120");
  await refused("L01", "line_disabled", "in-band", update("L01", { enabled: false }));
  await refused("L01", "line_paused", "in-band", update("L01", { paused_until: "2026-11-02T21:00:00Z" }));
  await refused("L04", "line_faulted", ["run-dialogs", "mister-fault"]);
  await refused("L04", "not_schedule_member", ["run-dialogs", "firmware-before-1343"], update("L04", { schedule_member: false }));
});

test("before the irrigation firmware L01 has no Duration: API-09 has no irrig_wall_duration_min row, so the dialog names the setting and sends {} plus the reason", async () => {
  const api = await open("L01", { fixture: "before-engine" });
  // API-09 is read when the dialog opens, not with the card. The bridge's rows retired once the engine was last-good
  // (#1165), so its answer carries no device value for the run.
  expect(requests(api, "API-09")).toEqual([]);
  press("Run… L01");
  await advance(0);
  expect(requests(api, "API-09")).toHaveLength(1);
  expect(screen.queryByLabelText("Duration")).toBeNull();
  expect(notes()).toEqual(["Runs for the Wall drip duration (irrig_wall_duration_min)"]);
  expect(title()).toBe("Run L01 Wall drip (clean)?");
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Wall plants are dry" } });
  press("Run");
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { reason: "Wall plants are dry" } })]);
  expect(dialog()).toBeNull();
  await readBack(5000);
  expect(screen.getByTestId("state").textContent).toBe("running");
});

test("before the irrigation firmware, without API-09 the dialog names the setting only, and still sends {}", async () => {
  const api = await open("L01", { fixture: "before-engine", fail: { "API-09": 503 } });
  press("Run… L01");
  await advance(0);
  expect(notes()).toEqual(["Runs for the Wall drip duration (irrig_wall_duration_min)"]);
  expect(title()).toBe("Run L01 Wall drip (clean)?");
  press("Run");
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", body: {} })]);
});

test("before the irrigation firmware the other lines say when Run arrives, for operators only", async () => {
  await open("L02", { fixture: "before-engine" });
  expect(text(document.querySelector("[data-arrives=run]"))).toBe(RUN_ARRIVES_TEXT);
  expect(screen.queryByRole("button")).toBeNull();
  cleanup(); stopFixtureApi();
  await open("L02", { fixture: "before-engine" }, { role: "viewer", writesEnabled: false });
  expect(document.querySelector("[data-arrives]")).toBeNull();
});

test("a viewer sees no Run…; with changes switched off Run… is disabled", async () => {
  await open("L01", { fixture: "viewer-session" }, { role: "viewer", writesEnabled: false });
  expect(screen.queryByRole("button")).toBeNull();
  expect(dialog()).toBeNull();
  cleanup(); stopFixtureApi();
  await open("L01", { fixture: "writes-off" }, { role: "operator", writesEnabled: false });
  expect(screen.getByRole("button", { name: "Run… L01" }).hasAttribute("disabled")).toBe(true);
});

type Row = { line_id: string; state: Record<string, unknown> };
/** API-IRR-01 keeps answering what `change` makes of it, whatever the run did. */
function pin(change: (row: Row) => void) {
  const fetcher = globalThis.fetch as (input: string, init?: RequestInit) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const answer = await fetcher(input, init);
    if (input !== "/api/v1/irrigation") return answer;
    const body = await answer.json() as { lines: Row[] };
    for (const row of body.lines) change(row);
    return { ...answer, headers: answer.headers, json: async () => body } as unknown as Response;
  }));
}
const idle = (row: Row, reason: string | null) => { if (row.line_id === "L01") row.state = { ...row.state, code: "idle", cause: null, ends_at: null, reason }; };
async function startRun(lineId: string, fixture: string | undefined, change: (row: Row) => void) {
  startFixtureApi({ fixture });
  pin(change);
  renderWithApi(<Card lineId={lineId} />);
  await advance(0);
  press(`Run… ${lineId}`);
  press(/^Run \d/);
  await advance(0);
}

test("a run that does not start within 30 s says why in the line's words, or Not started; a feed that opens its prewet has started", async () => {
  await startRun("L01", undefined, (row) => idle(row, null));
  await readBack(25_000);
  expect(text(document.querySelector("[data-run-note]"))).toBe("Waiting for the valve to open");
  await readBack(5000);
  expect(text(document.querySelector("[data-run-note]"))).toBe("Not started");
  cleanup(); stopFixtureApi();
  await startRun("L01", undefined, (row) => idle(row, "someone inside (Frigate)"));
  await readBack(30_000);
  expect(text(document.querySelector("[data-run-note]"))).toBe("someone inside (Frigate)");
  cleanup(); stopFixtureApi();
  // L02 itself reads idle while its clean partner L01 prewets: the run has started.
  await startRun("L02", "run-dialogs", (row) => {
    if (row.line_id === "L02") row.state = { ...row.state, code: "idle", cause: null, ends_at: null };
    if (row.line_id === "L01") row.state = { ...row.state, code: "running", cause: "prewet", ends_at: null };
  });
  await readBack(5000);
  expect(document.querySelector("[data-run-note]")).toBeNull();
  await readBack(30_000);
  expect(document.querySelector("[data-run-note]")).toBeNull();
});

test("a run that starts after its 30 s drops Not started on the card's own poll, and the note stays gone when the run ends", async () => {
  let code = "idle";
  await startRun("L01", undefined, (row) => { if (row.line_id === "L01") row.state = { ...row.state, code, cause: null, ends_at: null }; });
  await readBack(30_000);
  expect(text(document.querySelector("[data-run-note]"))).toBe("Not started");
  code = "running";
  await readBack(15_000);
  expect(screen.getByTestId("state").textContent).toBe("running");
  expect(document.querySelector("[data-run-note]")).toBeNull();
  code = "idle";
  await readBack(15_000);
  expect(screen.getByTestId("state").textContent).toBe("idle");
  expect(document.querySelector("[data-run-note]")).toBeNull();
});

test("Cancel while the run is out: its refusal still reads, on the card, and the next opening starts with none", async () => {
  await open("L02", { fixture: "manifold-busy" });
  press("Run… L02");
  press("Run 10 min");
  press("Cancel");
  expect(dialog()).toBeNull();
  await advance(0);
  expect(text(document.querySelector(".field-error[role=alert]"))).toBe("L01 holds the drip manifold until 10:49; stop it or try then");
  press("Run… L02");
  expect(document.querySelector(".field-error")).toBeNull();
});
