// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
/**
 * The write hooks (#1125) against #1122's fixture API in process: what each sends (operationId, body and
 * Idempotency-Key, from GET /__requests), the feedback it shows, the 5 s readback cadence, and the Stop result.
 */
import { useState } from "react";
import { QueryClient } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AlertRow } from "../components/alert-row";
import { ReadbackBadge } from "../components/badge";
import { Button } from "../components/button";
import { EditDrawer, ManualToggle, MANUAL_DEFAULT, NumberField, Reason } from "../components/forms";
import { StateReasonLine } from "../components/state-reason-line";
import { text } from "../components/test-render";
import type { StateReason, Alert as VocabAlert } from "./vocab";
import type { IrrigationStatus, Setting } from "./operator-api";
import { readKey, useApiRead, useIrrigationRead, useNowRead, useSchedulesRead, useSettingsRead } from "./queries";
import { parseDecimal } from "./setting-values";
import { NOW_POLL_MS, NOW_QUERY_KEY, fetchNow } from "./shell";
import { NOW, UUID, advance, renderWithApi, requests, sentAt, startFixtureApi, stopFixtureApi, writes } from "./test-fixture-api";
import { usePolledQuery } from "./use-current";
import { words } from "./words";
import {
  COMMAND_WINDOW_MS, NOT_STARTED, defaultReadback, errorFor, formError, newestNow, problemMessage, programBody, programSyncText, readback, runningLines, settingToast, stopResult,
  useCommand, useProgramWrite, useRecordWrite, useSettingWrite, useStop,
} from "./writes";

afterEach(() => { cleanup(); stopFixtureApi(); });
/** Each toast in the polite (`status`) or assertive (`alert`) stack: its message and guidance, without the × button. */
const toasts = (role: "status" | "alert") => [...screen.getByRole(role).querySelectorAll(".toast-body")]
  .map((body) => [...body.querySelectorAll("p")].map((line) => text(line)).join(" "));
const dialogOpen = () => document.querySelector("dialog")?.hasAttribute("open") ?? false;

// ---------------------------------------------------------------------------------------------------- S harness
/** A setting row with its badge and reason line, and the edit drawer the Settings screen (#1132) composes. */
function SettingRow({ param, bounds = true }: { param: string; bounds?: boolean }) {
  const settings = useSettingsRead();
  const [open, setOpen] = useState(false);
  const row = settings.data?.items.find((item) => item.param === param);
  if (!row) return <p>Loading</p>;
  const setting = bounds ? row : { ...row, min: null, max: null };
  return <div>
    <p data-testid="row"><ReadbackBadge state={row.state} readbackVia={row.readback_via} /> <StateReasonLine state={row.state} reason={row.desired?.state_reason as StateReason | null | undefined} requestedAt={row.desired?.created_at} unit={row.unit} /></p>
    <Button write onClick={() => setOpen(true)}>Edit</Button>
    <SettingDrawer setting={setting} open={open} onClose={() => setOpen(false)} />
  </div>;
}
function SettingDrawer({ setting, open, onClose }: { setting: Setting; open: boolean; onClose: () => void }) {
  const [value, setValue] = useState(setting.readback?.value === null || setting.readback?.value === undefined ? "" : String(setting.readback.value));
  const [manual, setManual] = useState(MANUAL_DEFAULT);
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState<string | null>(null);
  const write = useSettingWrite(setting, { onSaved: onClose });
  const save = () => {
    const number = parseDecimal(value);
    setTyped(number === null ? "Enter a number" : null);
    if (number !== null) void write.save({ value: number, manual, reason });
  };
  return <EditDrawer open={open} onClose={onClose} title="Edit setting" heading={setting.label} graph={{ param: setting.param, unit: setting.unit }} onSave={save}
    saving={write.sending} error={formError(write.error, ["value", "reason"])} actions={<Button write onClick={() => void write.release({ reason })}>Release to auto</Button>}>
    <NumberField label="New value" value={value} onChange={setValue} min={setting.min} max={setting.max} step={setting.step} unit={setting.unit} error={typed ?? errorFor(write.error, "value")} />
    <ManualToggle checked={manual} onChange={setManual} />
    <Reason value={reason} onChange={setReason} error={errorFor(write.error, "reason")} />
  </EditDrawer>;
}
async function openAndType(value: string) {
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("New value"), { target: { value } });
}
const badge = () => screen.getByTestId("row").querySelector("[data-badge]")?.getAttribute("data-badge");

test("Save always sends manual, ticked by default and false when unticked; a double tap sends one request with one key", async () => {
  const api = startFixtureApi();
  renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  await openAndType("1.6");
  const save = screen.getByRole("button", { name: "Save" });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(screen.getByRole("button", { name: "Sending…" }).hasAttribute("disabled")).toBe(true);
  await advance(0);
  const sentWrites = writes(api);
  expect(sentWrites).toHaveLength(1);
  expect(sentWrites[0]).toMatchObject({ method: "PUT", operationId: "API-10", path: "/api/v1/settings/mister_engage_kpa", body: { value: 1.6, manual: true } });
  expect(sentWrites[0].idempotency_key).toMatch(UUID);
  expect(dialogOpen()).toBe(false);
  await openAndType("1.65");
  fireEvent.click(screen.getByRole("checkbox", { name: "Let Iris change this" }));
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Vandas drying" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  const [first, second] = requests(api, "API-10");
  expect(second.body).toEqual({ value: 1.65, manual: false, reason: "Vandas drying" });
  expect(second.idempotency_key).toMatch(UUID);
  expect(second.idempotency_key).not.toBe(first.idempotency_key);
});

test("requested-to-confirmed: 1.53 closes the drawer with the snapped toast; the row polls API-09 every 5 s and turns CONFIRMED on the third read", async () => {
  const api = startFixtureApi({ fixture: "requested-to-confirmed" });
  renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  expect(sentAt("API-09")).toEqual([0]);
  await advance(1000);
  await openAndType("1.53");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(requests(api, "API-10")[0].body).toEqual({ value: 1.53, manual: true });
  expect(dialogOpen()).toBe(false);
  expect(toasts("status")).toEqual(["Requested 1.55 kPa for VPD Mister Engage (snapped from 1.53)"]);
  // The first read after the write is at once; TanStack renders each answer on a timer a moment later.
  const after = () => sentAt("API-09").filter((at) => at >= 1000);
  await advance(1);
  expect(after()).toEqual([1000]);
  expect(badge()).toBe("requested");
  await advance(4998);
  expect(after()).toHaveLength(1);
  await advance(3);
  expect(after()).toHaveLength(2);
  expect(badge()).toBe("requested");
  await advance(4998);
  await advance(3);
  const reads = after();
  expect(reads).toHaveLength(3);
  expect(badge()).toBe("confirmed");
  // Every 5 s while the row read REQUESTED.
  expect(reads[1] - reads[0]).toBeGreaterThanOrEqual(5000);
  expect(reads[1] - reads[0]).toBeLessThanOrEqual(5001);
  expect(reads[2] - reads[1]).toBe(5000);
  // Settled: back to the 60 s cadence.
  await advance(30_000);
  expect(after()).toHaveLength(3);
  // The success toast is polite and leaves after 5 s.
  expect(toasts("status")).toEqual([]);
});

test("with confirm false the row turns NOT CONFIRMED with its reason, and there is no Send again", async () => {
  startFixtureApi({ fixture: "requested-to-confirmed", confirm: false });
  renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  await openAndType("1.53");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  await advance(10_000);
  await advance(3);
  expect(badge()).toBe("not_confirmed");
  expect(text(screen.getByTestId("row").querySelector(".state-reason"))).toBe("Sent 10:42; no matching readback yet");
  expect(screen.queryByRole("button", { name: /send again/i })).toBeNull();
});

test("2.60 reads \"Must be 0.50–2.50 kPa\" beside the field and nothing is sent; the server's 422 reads the same and keeps the drawer open", async () => {
  const api = startFixtureApi();
  const view = renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  await openAndType("2.60");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  const field = screen.getByLabelText("New value");
  const error = screen.getByText("Must be 0.50–2.50 kPa", { normalizer: (value) => value.replaceAll("\u00a0", " ") });
  expect(field.getAttribute("aria-invalid")).toBe("true");
  expect(field.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
  expect(writes(api)).toEqual([]);
  expect(dialogOpen()).toBe(true);
  view.unmount();
  // Bounds the client does not know: the server refuses, and its min, max and the unit read the same words.
  renderWithApi(<SettingRow param="mister_engage_kpa" bounds={false} />);
  await advance(0);
  await openAndType("2.60");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-10", body: { value: 2.6, manual: true } })]);
  const refused = screen.getByText("Must be 0.50–2.50 kPa", { normalizer: (value) => value.replaceAll("\u00a0", " ") });
  expect(screen.getByLabelText("New value").getAttribute("aria-describedby")?.split(" ")).toContain(refused.id);
  expect(dialogOpen()).toBe(true);
  await advance(30_000);
  expect(writes(api)).toHaveLength(1);
});

test("503 writes_disabled reads \"Changes are switched off\"; 503 unavailable shows its detail; the drawer stays open", async () => {
  startFixtureApi({ fixture: "writes-off" });
  const view = renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  await openAndType("1.6");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(within(document.querySelector("dialog")!).getByRole("alert").textContent).toBe("Changes are switched off");
  expect(dialogOpen()).toBe(true);
  view.unmount();
  stopFixtureApi();
  startFixtureApi({ fail: { "API-10": 503 } });
  renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  await openAndType("1.6");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(within(document.querySelector("dialog")!).getByRole("alert").textContent).toBe("The fixture fails API-10 with 503");
});

test("a site cap before the irrigation firmware saves, reads REQUESTED with the no_engine reason at once, and does not poll fast", async () => {
  const api = startFixtureApi({ fixture: "before-engine" });
  renderWithApi(<SettingRow param="sched_mist_rh_max_pct" />);
  await advance(0);
  await openAndType("88");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await advance(0);
  expect(requests(api, "API-10")[0]).toMatchObject({ path: "/api/v1/settings/sched_mist_rh_max_pct", body: { value: 88, manual: true } });
  expect(toasts("status")).toEqual(["Requested 88% for Scheduled-mist RH ceiling"]);
  expect(badge()).toBe("requested");
  expect(text(screen.getByTestId("row").querySelector(".state-reason"))).toBe("Applies with the irrigation firmware");
  const reads = sentAt("API-09").length;
  await advance(20_000);
  expect(sentAt("API-09")).toHaveLength(reads);
});

test("Release to auto sends API-11 with the reason and no body, and toasts the release", async () => {
  const api = startFixtureApi({ fixture: "superseded-lane" });
  renderWithApi(<SettingRow param="mister_engage_kpa" />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Reason (optional)"), { target: { value: "Back to the plan" } });
  fireEvent.click(screen.getByRole("button", { name: "Release to auto" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ method: "DELETE", operationId: "API-11", path: "/api/v1/settings/mister_engage_kpa?reason=Back+to+the+plan", body: null })]);
  expect(toasts("status")).toEqual(["Released VPD Mister Engage: plan, band or default applies"]);
  expect(settingToast({ value: 1, snapped_from: null }, { label: "GL Main Auto Mode", kind: "switch" })).toBe("Requested ON for GL Main Auto Mode");
  expect(settingToast({ value: 60, snapped_from: 48.75 }, { label: "Mister Engage Delay", kind: "numeric", unit: "s", step: 30 })).toBe("Requested 60\u00a0s for Mister Engage Delay (snapped from 48.75)");
});

// ---------------------------------------------------------------------------------------------------- P
test("program bodies: schedules and line definition edits always carry manual; a pause, a resume or a check alone carries none", () => {
  expect(programBody("API-IRR-03", { line_id: "L01", days: ["mon"], on_s: 720 })).toEqual({ line_id: "L01", days: ["mon"], on_s: 720, manual: true });
  expect(programBody("API-IRR-04", { enabled: false, manual: false })).toEqual({ enabled: false, manual: false });
  expect(programBody("API-IRR-08", { vpd_member: true })).toEqual({ vpd_member: true, manual: true });
  expect(programBody("API-IRR-08", { daily_cap_min: 45, manual: false })).toEqual({ daily_cap_min: 45, manual: false });
  expect(programBody("API-IRR-08", { paused_until: "2026-11-03T07:00:00Z", manual: true })).toEqual({ paused_until: "2026-11-03T07:00:00Z" });
  expect(programBody("API-IRR-08", { checked: true, check_note: "Flushed" })).toEqual({ checked: true, check_note: "Flushed" });
  expect(programBody("API-IRR-05", undefined)).toBeUndefined();
});

function Program() {
  const status = useIrrigationRead();
  useSchedulesRead({ days: 7 });
  const schedules = useProgramWrite("API-IRR-03");
  const lines = useProgramWrite("API-IRR-08");
  const line = status.data?.lines.find((item) => item.line_id === "L07");
  return <div>
    <p data-testid="sync">{line ? `${line.sync} · ${programSyncText(line.sync, status.data) ?? ""}` : "Loading"}</p>
    <Button write onClick={() => void schedules.send({}, { line_id: "L01", days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 720 })}>New schedule</Button>
    <Button write busy={lines.sending} onClick={() => void lines.send({ line_id: "L07" }, { vpd_member: true })}>VPD control</Button>
    <Button write onClick={() => void lines.send({ line_id: "all" }, { paused_until: new Date(NOW + 3_600_000).toISOString() })}>Pause all</Button>
  </div>;
}

test("P: a schedule and an inline switch send manual true, a pause none; the item reads \"rev 45 sending\" and API-IRR-01 and -02 poll every 5 s", async () => {
  const api = startFixtureApi();
  renderWithApi(<Program />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "VPD control" }));
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Pause all" }));
  await advance(0);
  expect(writes(api).map((row) => [row.operationId, row.body])).toEqual([
    ["API-IRR-08", { vpd_member: true, manual: true }],
    ["API-IRR-03", { line_id: "L01", days: ["mon", "wed", "fri"], start: { kind: "sunrise", offset_min: 60 }, on_s: 720, manual: true }],
    ["API-IRR-08", { paused_until: new Date(NOW + 3_600_000).toISOString() }],
  ]);
  // Three writes, three revisions on top of rev 42; the pause is the newest, still sending.
  expect(screen.getByTestId("sync").textContent).toBe("requested · rev 45 sending");
  const irrigation = sentAt("API-IRR-01").length;
  const schedules = sentAt("API-IRR-02").length;
  await advance(5000);
  expect(sentAt("API-IRR-01")).toHaveLength(irrigation + 1);
  expect(sentAt("API-IRR-02")).toHaveLength(schedules + 1);
});

test("P before the irrigation firmware: the item stays REQUESTED with \"Applies with the irrigation firmware\" and polls slowly", async () => {
  startFixtureApi({ fixture: "before-engine" });
  renderWithApi(<Program />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "VPD control" }));
  await advance(0);
  expect(screen.getByTestId("sync").textContent).toBe("requested · Applies with the irrigation firmware");
  const reads = sentAt("API-IRR-01").length;
  await advance(10_000);
  expect(sentAt("API-IRR-01")).toHaveLength(reads);
  expect(programSyncText("not_confirmed", { engine: true, program: { state: "not_confirmed", revision: 43, desired_sha256: "x", src: "nvs", reject_code: "crc_mismatch" } })).toBe("Not confirmed: crc_mismatch");
  expect(programSyncText("confirmed", null)).toBeNull();
});

// ---------------------------------------------------------------------------------------------------- C
function Command({ lineId, late }: { lineId: string; late?: boolean }) {
  const status = useIrrigationRead();
  const command = useCommand({
    operation: "API-IRR-06",
    readback: late ? readback({ operationId: "API-IRR-01", done: () => false, reason: (data) => data.lines.find((item) => item.line_id === lineId)?.state.reason ?? null }) : undefined,
  });
  const clear = useCommand({ operation: "API-IRR-13" });
  const line = status.data?.lines.find((item) => item.line_id === lineId);
  return <div>
    <p data-testid="command">{line?.state.code ?? "—"} · {command.phase}{command.message ? ` · ${command.message}` : ""}</p>
    <Button write busy={command.sending} onClick={() => void command.send({ line_id: lineId }, { minutes: 5, manual: true } as { minutes: number })}>Run</Button>
    <Button write busy={clear.sending} onClick={() => void clear.send({}, { line_id: "L04" })}>Clear fault</Button>
    <p data-testid="clear">{clear.phase}{clear.error ? ` · ${clear.error.message}` : ""}</p>
  </div>;
}

test("C: Run carries no manual field, reads \"Sending…\" until the 202, then reads API-IRR-01 every 5 s until the relay edge reads RUNNING", async () => {
  const api = startFixtureApi();
  renderWithApi(<Command lineId="L01" />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  expect(screen.getByRole("button", { name: "Sending…" })).toBeTruthy();
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-06", path: "/api/v1/irrigation/lines/L01/run", body: { minutes: 5 } })]);
  expect(screen.getByTestId("command").textContent).toBe("idle · waiting");
  const reads = sentAt("API-IRR-01").length;
  await advance(5000);
  expect(sentAt("API-IRR-01")).toHaveLength(reads + 1);
  expect(screen.getByTestId("command").textContent).toBe("running · done");
});

test("C: nothing changed within 30 s reads the line's reason in words, or \"Not started\"", async () => {
  startFixtureApi();
  renderWithApi(<Command lineId="L01" late />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Run" }));
  await advance(0);
  await advance(COMMAND_WINDOW_MS - 5000);
  expect(screen.getByTestId("command").textContent).toContain("waiting");
  await advance(5000);
  expect(screen.getByTestId("command").textContent).toBe(`running · late · ${NOT_STARTED}`);
});

/** One light's Turn on with API-13's Lutron state as its readback, reading it back after a 503 too when `after` says so. */
function LightCommandHarness({ after }: { after: boolean }) {
  const command = useCommand({
    operation: "API-20",
    readback: (params) => readback({ operationId: "API-13", done: (answer) => answer.items.find((item) => item.equipment === "grow_light_main")?.state === params.state }),
    readbackAfter: after ? (error) => error.code === "unavailable" : undefined,
  });
  return <div>
    <p data-testid="light">{command.phase}{command.error ? ` · ${command.error.message}` : ""}</p>
    <Button write busy={command.sending} onClick={() => void command.send({ light: "main", state: "on" }, undefined)}>Turn on</Button>
    <Button onClick={command.reset}>Reset</Button>
  </div>;
}
const lightState = () => screen.getByTestId("light").textContent;
const turnOn = async () => { fireEvent.click(screen.getByRole("button", { name: "Turn on" })); await advance(0); };

test("C: a failure readbackAfter names reads back all the same with its words beside it, and goes late after 30 s", async () => {
  startFixtureApi({ fixture: "lights-not-confirmed" });
  renderWithApi(<LightCommandHarness after />);
  await turnOn();
  expect(lightState()).toBe("waiting · Lutron did not reach the state");
  const reads = sentAt("API-13").length;
  await advance(COMMAND_WINDOW_MS);
  expect(sentAt("API-13")).toHaveLength(reads + 6);
  expect(lightState()).toBe("late · Lutron did not reach the state");
});

test("C: a late switch after a failure clears its words", async () => {
  const api = startFixtureApi({ fixture: "lights-ha-fail" });
  renderWithApi(<LightCommandHarness after />);
  await turnOn();
  expect(lightState()).toBe("waiting · Lutron did not switch: Home Assistant did not answer");
  // Lutron answers late: the readback turns on.
  api.respond({ method: "POST", url: "/__control", body: { fixture: "lights-confirmed" } });
  api.respond({ method: "POST", url: "/v1/equipment/lights/main/on" });
  await advance(5000);
  await advance(1);
  expect(lightState()).toBe("done");
});

test("C: a failure nothing names reads as failed with no readback, and ends the readback an earlier failure left running", async () => {
  startFixtureApi({ fixture: "lights-ha-fail" });
  const first = renderWithApi(<LightCommandHarness after={false} />);
  await turnOn();
  expect(lightState()).toBe("failed · Lutron did not switch: Home Assistant did not answer");
  const reads = sentAt("API-13").length;
  await advance(COMMAND_WINDOW_MS);
  expect(sentAt("API-13")).toHaveLength(reads);
  first.unmount();
  const api = startFixtureApi({ fixture: "lights-ha-fail" });
  renderWithApi(<LightCommandHarness after />);
  await turnOn();
  expect(lightState()).toContain("waiting");
  // Changes switched off while it reads back: nothing reached the light, so no readback of its own, and the old one ends.
  api.respond({ method: "POST", url: "/__control", body: { fixture: "writes-off" } });
  await turnOn();
  expect(lightState()).toBe("failed · Changes are switched off");
  const after = sentAt("API-13").length;
  await advance(COMMAND_WINDOW_MS);
  expect(sentAt("API-13")).toHaveLength(after);
});

test("C: reset ends a readback and its words", async () => {
  startFixtureApi({ fixture: "lights-not-confirmed" });
  renderWithApi(<LightCommandHarness after />);
  await turnOn();
  expect(lightState()).toBe("waiting · Lutron did not reach the state");
  fireEvent.click(screen.getByRole("button", { name: "Reset" }));
  await advance(0);
  expect(lightState()).toBe("idle");
  const reads = sentAt("API-13").length;
  await advance(COMMAND_WINDOW_MS);
  expect(sentAt("API-13")).toHaveLength(reads);
  expect(lightState()).toBe("idle");
});

test("C: a command answered after its screen left reads nothing back", async () => {
  const api = startFixtureApi({ fixture: "lights-confirmed" });
  const view = renderWithApi(<LightCommandHarness after />);
  fireEvent.click(screen.getByRole("button", { name: "Turn on" }));
  view.unmount();
  await advance(0);
  expect(requests(api, "API-20")).toHaveLength(1);
  const reads = sentAt("API-13").length;
  await advance(COMMAND_WINDOW_MS);
  expect(sentAt("API-13")).toHaveLength(reads);
});

test("a Problem's result reads in the light_result words; an unknown one raw; none its detail", () => {
  const unavailable = { type: "about:blank", title: "Unavailable", status: 503, code: "unavailable", retryable: true, min: null, max: null, current: null, retry_after_s: null };
  expect(problemMessage({ ...unavailable, detail: "The light may still switch", result: "timeout" })).toBe("No answer yet: Lutron may still switch");
  expect(problemMessage({ ...unavailable, detail: "The light may still switch", result: "fuse_blown" })).toBe("fuse_blown");
  expect(problemMessage({ ...unavailable, detail: "arrives with the irrigation firmware" })).toBe("arrives with the irrigation firmware");
});

test("C: Clear fault sends one API-IRR-13 request with no manual; before the engine its 503 shows the detail", async () => {
  const api = startFixtureApi({ fixture: "mister-fault" });
  const view = renderWithApi(<Command lineId="L04" />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Clear fault" }));
  await advance(0);
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-13", body: { line_id: "L04" } })]);
  await advance(5000);
  expect(screen.getByTestId("clear").textContent).toBe("done");
  view.unmount();
  stopFixtureApi();
  startFixtureApi({ fixture: "before-engine" });
  renderWithApi(<Command lineId="L04" />);
  await advance(0);
  fireEvent.click(screen.getByRole("button", { name: "Clear fault" }));
  await advance(0);
  expect(screen.getByTestId("clear").textContent).toBe("failed · arrives with the irrigation firmware");
});

test("C: a clear with no line_id waits for every latch, the fertilizer misters' included; a back-off is no latch", () => {
  const status = (faults: Record<string, string>) => ({
    lines: ["L04", "L07", "L08", "L09", "L10"].map((id) => ({ line_id: id, fault: faults[id] ?? null, state: { code: faults[id] ? "blocked" : "idle", reason: faults[id] ? `${id} faulted` : null } })),
  }) as unknown as IrrigationStatus;
  const all = defaultReadback("API-IRR-13", {}, {})!;
  expect(all.done(status({ L08: "mister_fault" }))).toBe(false);
  expect(all.reason?.(status({ L08: "mister_fault" }))).toBe("L08 faulted");
  expect(all.done(status({ L04: "backing_off" }))).toBe(true);
  expect(all.done(status({}))).toBe(true);
  const one = defaultReadback("API-IRR-13", {}, { line_id: "L07" })!;
  expect(one.done(status({ L08: "mister_fault" }))).toBe(true);
  expect(one.done(status({ L07: "mister_fault" }))).toBe(false);
});

test("two presses before a render send one request with one key: Save, Run and Stop", async () => {
  const api = startFixtureApi();
  function Presses() {
    const settings = useSettingsRead();
    useNowRead();
    const row = settings.data?.items.find((item) => item.param === "mister_engage_kpa");
    const write = useSettingWrite(row ?? { param: "mister_engage_kpa", label: "VPD Mister Engage", kind: "numeric", unit: "kPa", step: 0.05, min: 0.5, max: 2.5 });
    const run = useCommand({ operation: "API-IRR-06" });
    const { stop } = useStop();
    // Each handler presses twice in one event, so the second press finds no busy state rendered yet.
    return <div>
      <button type="button" onClick={() => { void write.save({ value: 1.6 }); void write.save({ value: 1.6 }); }}>Save twice</button>
      <button type="button" onClick={() => { void run.send({ line_id: "L01" }, { minutes: 5 }); void run.send({ line_id: "L01" }, { minutes: 5 }); }}>Run twice</button>
      <button type="button" onClick={() => { void stop(); void stop(); }}>Stop twice</button>
    </div>;
  }
  renderWithApi(<Presses />);
  await advance(0);
  for (const name of ["Save twice", "Run twice", "Stop twice"]) {
    fireEvent.click(screen.getByRole("button", { name }));
    await advance(0);
  }
  const sent = writes(api);
  expect(sent.map((row) => row.operationId)).toEqual(["API-10", "API-IRR-06", "API-IRR-07"]);
  expect(new Set(sent.map((row) => row.idempotency_key)).size).toBe(3);
});

test("C: manifold_busy reads the API's sentence naming the holder; a detail with no end time gains one from retry_after_s", () => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  // #1341: the holder reads by its name, whichever API build wrote the detail with its relay code.
  const busy = { type: "about:blank", title: "Manifold busy", status: 422, code: "manifold_busy", retryable: true, min: null, max: null, current: null };
  expect(problemMessage({ ...busy, detail: "L01 holds the drip manifold until 07:42; stop it or try then", retry_after_s: 60 })).toBe("Wall drip holds the drip manifold until 07:42; stop it or try then");
  expect(problemMessage({ ...busy, detail: "L01 holds the drip manifold", retry_after_s: 420 })).toBe("Wall drip holds the drip manifold until 10:49; stop it or try then");
  expect(problemMessage({ ...busy, detail: "L01 holds the drip manifold; stop it or try again", retry_after_s: 420 })).toBe("Wall drip holds the drip manifold until 10:49; stop it or try then");
  expect(problemMessage({ ...busy, detail: "L01 holds the drip manifold", retry_after_s: null })).toBe("Wall drip holds the drip manifold");
  expect(problemMessage({ ...busy, retry_after_s: null })).toBe(words("problem_code", "manifold_busy"));
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------------------------------- R
function Alerts() {
  const alerts = useApiRead("API-14", {}, { intervalMs: 15_000 });
  const record = useRecordWrite();
  return <ul>{alerts.data?.items.map((item) => <AlertRow key={item.id} alert={item as unknown as VocabAlert}
    acknowledge={item.status === "open" ? <><Button write busy={record.sending} onClick={() => void record.acknowledge(item.id, "On it")}>Acknowledge</Button>
      <Button write onClick={() => void record.resolve(item.id)}>Resolve</Button></> : undefined} />)}</ul>;
}

test("R: Acknowledge saves at once, its row reads \"Acknowledged by jason · 10:42\" from the returned Alert; Resolve sends no note", async () => {
  const api = startFixtureApi({ fixture: "alerts-merged" });
  const { client } = renderWithApi(<Alerts />);
  await advance(0);
  fireEvent.click(screen.getAllByRole("button", { name: "Acknowledge" })[0]);
  await advance(0);
  const [ack] = writes(api);
  expect(ack).toMatchObject({ method: "POST", operationId: "API-15", path: "/api/v1/alerts/101/ack", body: { note: "On it" } });
  expect(ack.idempotency_key).toMatch(UUID);
  const cached = client.getQueryData<{ items: { id: number; acknowledged_by: string | null }[] }>(readKey("API-14"));
  expect(cached?.items.find((item) => item.id === 101)?.acknowledged_by).toBe("jason");
  expect(text(document.querySelector("li"))).toContain("Acknowledged by jason · 10:42");
  fireEvent.click(screen.getAllByRole("button", { name: "Resolve" })[0]);
  await advance(0);
  expect(writes(api)[1]).toMatchObject({ operationId: "API-16", body: null });
});

// ---------------------------------------------------------------------------------------------------- Stop
function Stop({ line }: { line?: string }) {
  useNowRead();
  useIrrigationRead();
  const { stop, sending } = useStop();
  return <Button variant="stop" write busy={sending} onClick={() => void stop(line ?? null)}>{line ? `Stop ${line}` : "Stop all water"}</Button>;
}
async function pressStop(name = "Stop all water") {
  fireEvent.click(screen.getByRole("button", { name }));
  await advance(0);
}

test("Stop: one tap sends one API-IRR-07 request with {} and no manual; the result stays until dismissed and offers Disable for each running line", async () => {
  const api = startFixtureApi({ fixture: "l01-running" });
  renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  expect(writes(api)).toEqual([expect.objectContaining({ method: "POST", operationId: "API-IRR-07", body: {} })]);
  expect(writes(api)[0].idempotency_key).toMatch(UUID);
  expect(toasts("status")).toEqual([]);
  await advance(5000);
  const stopped = "Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it.";
  expect(toasts("status")).toEqual([stopped]);
  await advance(120_000);
  expect(toasts("status")).toEqual([stopped]);
  fireEvent.click(screen.getByRole("button", { name: "Disable Wall drip" }));
  await advance(0);
  expect(writes(api)[1]).toMatchObject({ method: "PUT", operationId: "API-IRR-08", path: "/api/v1/irrigation/lines/L01", body: { enabled: false, manual: true } });
  expect(text(screen.getByRole("status"))).toContain("Wall drip: disable requested");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(toasts("status")).toEqual([]);
});

test("Stop before the irrigation firmware: \"VPD misting and fog can still start on their own.\" and no Disable", async () => {
  startFixtureApi({ fixture: ["l01-running", "before-engine"] });
  renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  await advance(5000);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting and fog can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
});

test("Stop with the ingestor not running: \"Stop saved but not sent: ingestor not running since 10:38\"", async () => {
  startFixtureApi({ fixture: "ingestor-not-running" });
  renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  await advance(5000);
  expect(toasts("alert")).toEqual(["Stop saved but not sent: ingestor not running since 10:38. Running drips end on their own timer; close the spigot if water must stop now."]);
});

test("Stop with the controller not reporting: \"The controller hasn't confirmed the stop. Close the spigot to stop water now.\"", async () => {
  startFixtureApi({ fixture: "controller-not-reporting" });
  renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  await advance(5000);
  expect(toasts("alert")).toEqual(["The controller hasn't confirmed the stop. Close the spigot to stop water now."]);
});

test("Stop with API-IRR-01 failing (as at R1): one request, the engine unknown, no Disable; with stop-unconfirmed, L01 still running after 30 s", async () => {
  const fail = { "API-IRR-01": 503 };
  const api = startFixtureApi({ fixture: "l01-running", fail });
  const view = renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  await advance(5000);
  expect(requests(api, "API-IRR-07")).toHaveLength(1);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own."]);
  expect(screen.queryByRole("button", { name: /^Disable/ })).toBeNull();
  view.unmount();
  stopFixtureApi();
  startFixtureApi({ fixture: ["l01-running", "stop-unconfirmed"], fail });
  renderWithApi(<Stop />);
  await advance(0);
  await pressStop();
  await advance(25_000);
  expect([...toasts("alert"), ...toasts("status")]).toEqual([]);
  await advance(5000);
  expect(toasts("alert")).toEqual(["The controller hasn't confirmed the stop. Close the spigot to stop water now."]);
});

test("Stop is never disabled by the writes flag, carries {line_id} for one line, and viewers see none", async () => {
  const api = startFixtureApi({ fixture: ["l01-running", "writes-off"] });
  const view = renderWithApi(<Stop line="L01" />, { role: "operator", writesEnabled: false });
  await advance(0);
  const button = screen.getByRole("button", { name: "Stop L01" });
  expect(button.hasAttribute("disabled")).toBe(false);
  await pressStop("Stop L01");
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-07", body: { line_id: "L01" } })]);
  await advance(5000);
  expect(toasts("status")[0]).toMatch(/^Stopped every running valve\. /);
  // The Disable control is a write control: disabled while changes are switched off.
  expect(screen.getByRole("button", { name: "Disable Wall drip" }).hasAttribute("disabled")).toBe(true);
  view.unmount();
  renderWithApi(<Stop />, { role: "viewer", writesEnabled: true });
  expect(screen.queryByRole("button")).toBeNull();
});

test("Stop notes what was running from the shell's own API-02 poll, so a screen with no read of its own still offers Disable (#1135)", async () => {
  const api = startFixtureApi({ fixture: "l01-running" });
  // The shell polls API-02 under its own key on every page; Water reads API-IRR-01 and never starts a second API-02 poll.
  function ShellStop() {
    usePolledQuery(NOW_QUERY_KEY, fetchNow, NOW_POLL_MS);
    useIrrigationRead();
    const { stop, sending } = useStop();
    return <Button variant="stop" write busy={sending} onClick={() => void stop()}>Stop all water</Button>;
  }
  renderWithApi(<ShellStop />);
  await advance(0);
  await pressStop();
  expect(writes(api)).toEqual([expect.objectContaining({ operationId: "API-IRR-07", body: {} })]);
  await advance(5000);
  expect(toasts("status")).toEqual(["Stopped every running valve. VPD misting, fog and the next scheduled run can still start on their own; to keep a line off, disable it."]);
  expect(screen.getByRole("button", { name: "Disable Wall drip" })).toBeTruthy();
});

test("newestNow takes the newest API-02 answer the page holds, the shell's or a screen's", () => {
  const client = new QueryClient();
  expect(newestNow(client)).toBeUndefined();
  client.setQueryData(NOW_QUERY_KEY, { running: [{ equipment: "drip_wall", line_id: "L01" }] }, { updatedAt: 1000 });
  expect(runningLines(newestNow(client))).toEqual(["L01"]);
  client.setQueryData(readKey("API-02"), { running: [] }, { updatedAt: 2000 });
  expect(runningLines(newestNow(client))).toEqual([]);
  client.setQueryData(NOW_QUERY_KEY, { running: [{ equipment: "mister_center", line_id: "L04" }] }, { updatedAt: 3000 });
  expect(runningLines(newestNow(client))).toEqual(["L04"]);
});

test("the Stop result is the first rule that holds; L03 and fog are never offered Disable", () => {
  const fresh = { ingestor: { observed_at: new Date(NOW - 20_000).toISOString(), age_s: 20, stale: false }, climate: { observed_at: new Date(NOW - 20_000).toISOString(), age_s: 20, stale: false } };
  const base = { running: ["L01", "L03", "L02", "L11"], engine: true, confirmed: true, freshness: fresh };
  expect(stopResult(base)).toMatchObject({ kind: "success", disable: ["L01", "L02"] });
  expect(stopResult({ ...base, confirmed: false })).toMatchObject({ kind: "failure", message: "The controller hasn't confirmed the stop." });
  expect(stopResult({ ...base, freshness: null })).toMatchObject({ kind: "failure", message: "The controller hasn't confirmed the stop." });
  expect(stopResult({ ...base, freshness: { ...fresh, climate: { ...fresh.climate, age_s: 301 } } })).toMatchObject({ message: "The controller hasn't confirmed the stop." });
  expect(stopResult({ ...base, confirmed: false, freshness: { ...fresh, ingestor: { ...fresh.ingestor, age_s: 181 } } })).toMatchObject({ message: "Stop saved but not sent: ingestor not running since 10:41." });
  expect(stopResult({ ...base, engine: false })).toMatchObject({ detail: "VPD misting and fog can still start on their own.", disable: [] });
  expect(stopResult({ ...base, engine: null })).toMatchObject({ detail: "VPD misting, fog and the next scheduled run can still start on their own.", disable: [] });
});
