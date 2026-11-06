// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The form controls (#1125, design system §4.3): NumberField, Switch, Select, Reason, ManualToggle, TimeField and the
// weekday chips. The drawer, the dialog and the writes are tested beside them.
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { useState } from "react";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { renderUi, text } from "../test-render";
import {
  boundsHint, LET_IRIS_HELP, ManualToggle, MANUAL_DEFAULT, NumberField, Reason, resolvedStart, Select, startDraft, startFromDraft, stepValue, Switch, TimeField,
  WeekdayChips, type StartDraft, type Weekday,
} from "./index";

afterEach(cleanup);
const OPERATOR = { role: "operator", writesEnabled: true } as const;
const describedIds = (element: Element) => (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
const describedText = (element: Element) => describedIds(element).map((id) => text(document.getElementById(id))).join(" | ");

function Kpa(props: { initial?: string; error?: string | null; saved?: { value: number; snappedFrom?: number | null } }) {
  const [value, setValue] = useState(props.initial ?? "1.53");
  return <NumberField label="New value" value={value} onChange={setValue} min={0.5} max={2.5} step={0.05} unit="kPa" error={props.error} saved={props.saved} />;
}

test("NumberField: a decimal input with its unit and the hint \"Allowed 0.50–2.50 kPa · step 0.05\"", () => {
  renderUi(<Kpa />);
  const input = screen.getByLabelText("New value");
  expect(input.getAttribute("inputmode")).toBe("decimal");
  expect(input.getAttribute("type")).toBe("text");
  expect(text(document.querySelector(".field-unit"))).toBe("kPa");
  expect(describedText(input)).toBe("kPa | Allowed 0.50–2.50 kPa · step 0.05");
  expect(input.hasAttribute("aria-invalid")).toBe(false);
  const hint = (...args: Parameters<typeof boundsHint>) => boundsHint(...args)?.replaceAll("\u00a0", " ");
  expect(hint(30, 300, 30, "s")).toBe("Allowed 30–300 s · step 30");
  expect(hint(75, 98, 1, "%")).toBe("Allowed 75–98% · step 1");
  expect(hint(null, null, 0.5, "°F")).toBe("Step 0.5");
  expect(hint(100, null, 10, "gal")).toBe("At least 100 gal · step 10");
  expect(boundsHint(null, null, null, null)).toBeNull();
});

test("NumberField: − and + move one step from the typed value without snapping, kept inside min and max", () => {
  renderUi(<Kpa />);
  const input = screen.getByLabelText("New value") as HTMLInputElement;
  fireEvent.click(screen.getByRole("button", { name: "Increase by 0.05" }));
  expect(input.value).toBe("1.58");
  fireEvent.click(screen.getByRole("button", { name: "Decrease by 0.05" }));
  fireEvent.click(screen.getByRole("button", { name: "Decrease by 0.05" }));
  expect(input.value).toBe("1.48");
  fireEvent.change(input, { target: { value: "2.48" } });
  fireEvent.click(screen.getByRole("button", { name: "Increase by 0.05" }));
  expect(input.value).toBe("2.50");
  expect(stepValue("", 1, { min: 0.5, max: 2.5, step: 0.05 })).toBe("0.50");
  expect(stepValue("abc", -1, { step: 30 })).toBe("0");
  expect(stepValue("60", 1, { min: 30, max: 300, step: 30 })).toBe("90");
});

test("NumberField: an error sits beside the field with aria-invalid and aria-describedby; a 200's snap reads \"Saved as\"", () => {
  renderUi(<Kpa error="Must be 0.50–2.50 kPa" />);
  const input = screen.getByLabelText("New value");
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(describedText(input)).toBe("kPa | Allowed 0.50–2.50 kPa · step 0.05 | Must be 0.50–2.50 kPa");
  expect(screen.getByRole("alert").textContent).toBe("Must be 0.50–2.50 kPa");
  cleanup();
  renderUi(<Kpa saved={{ value: 1.55, snappedFrom: 1.53 }} />);
  expect(text(screen.getByRole("status"))).toBe("Saved as 1.55 kPa (snapped from 1.53)");
  expect(describedText(screen.getByLabelText("New value"))).toContain("Saved as 1.55 kPa (snapped from 1.53)");
  cleanup();
  renderUi(<Kpa saved={{ value: 1.6, snappedFrom: null }} />);
  expect(text(screen.getByRole("status"))).toBe("Saved as 1.60 kPa");
});

test("Switch: role=switch with aria-checked and a visible ON or OFF word; an inline write switch follows the writes flag and roles", () => {
  const onChange = vi.fn();
  renderUi(<Switch label="VPD control" checked={false} onChange={onChange} />);
  const control = screen.getByRole("switch", { name: "VPD control" });
  expect(control.getAttribute("aria-checked")).toBe("false");
  expect(control.textContent).toBe("OFF");
  fireEvent.click(control);
  expect(onChange).toHaveBeenCalledWith(true);
  cleanup();
  renderUi(<Switch label="Schedule" checked onChange={onChange} write busy />, OPERATOR);
  const busy = screen.getByRole("switch", { name: "Schedule" });
  expect(busy.getAttribute("aria-checked")).toBe("true");
  expect(busy.textContent).toBe("Sending…");
  expect(busy.hasAttribute("disabled")).toBe(true);
  cleanup();
  renderUi(<Switch label="Enabled" checked onChange={onChange} write />, OPERATOR);
  expect(screen.getByRole("switch", { name: "Enabled" }).textContent).toBe("ON");
  expect(screen.getByRole("switch", { name: "Enabled" }).hasAttribute("disabled")).toBe(false);
  cleanup();
  renderUi(<Switch label="Enabled" checked onChange={onChange} write />, { role: "operator", writesEnabled: false });
  expect(screen.getByRole("switch", { name: "Enabled" }).hasAttribute("disabled")).toBe(true);
  cleanup();
  const viewer = renderUi(<Switch label="Enabled" checked onChange={onChange} write />, { role: "viewer", writesEnabled: true });
  expect(viewer.container.textContent).toBe("");
});

test("Select: a native select from Setting.options, read as numbers; an unknown value asks to choose", () => {
  const onChange = vi.fn();
  const options = [{ value: 0, label: "Off" }, { value: 1, label: "Auto" }, { value: 2, label: "On" }];
  renderUi(<Select label="Mode" value={1} options={options} onChange={onChange} />);
  const select = screen.getByLabelText("Mode") as HTMLSelectElement;
  expect(select.tagName).toBe("SELECT");
  expect(select.value).toBe("1");
  fireEvent.change(select, { target: { value: "2" } });
  expect(onChange).toHaveBeenCalledWith(2);
  cleanup();
  renderUi(<Select label="Mode" value={null} options={options} onChange={onChange} />);
  expect((screen.getByLabelText("Mode") as HTMLSelectElement).value).toBe("");
  expect(screen.getByRole("option", { name: "Choose…" })).toBeTruthy();
});

test("Reason is one optional line of at most 500 characters", () => {
  renderUi(<Reason value="" onChange={() => {}} />);
  const input = screen.getByLabelText("Reason (optional)");
  expect(input.getAttribute("type")).toBe("text");
  expect(input.getAttribute("maxlength")).toBe("500");
});

test("ManualToggle: \"Let Iris change this\", off by default on an edit form (manual); ticking it reports manual false (#1344)", () => {
  expect(MANUAL_DEFAULT).toBe(true);
  const seen: boolean[] = [];
  function Form() {
    const [manual, setManual] = useState(MANUAL_DEFAULT);
    return <ManualToggle checked={manual} onChange={(value) => { seen.push(value); setManual(value); }} />;
  }
  renderUi(<Form />);
  const box = screen.getByRole("checkbox", { name: "Let Iris change this" }) as HTMLInputElement;
  expect(box.checked).toBe(false);
  expect(box.getAttribute("aria-describedby") && document.getElementById(box.getAttribute("aria-describedby")!)?.textContent).toBe(LET_IRIS_HELP);
  fireEvent.click(box);
  expect(box.checked).toBe(true);
  expect(seen).toEqual([false]);
});

const SOLAR = { date: "2026-11-02", sunrise: "2026-11-02T13:35:00.000Z", solar_noon: "2026-11-02T18:47:00.000Z", sunset: "2026-11-02T23:58:00.000Z" };
function Start({ initial }: { initial: StartDraft }) {
  const [value, setValue] = useState(initial);
  return <TimeField value={value} onChange={setValue} solar={SOLAR} error={startFromDraft(value).error ?? null} />;
}

test("TimeField: \"At HH:MM\", or sunrise, solar noon or sunset with an offset, today's resolved clock time beside it", () => {
  renderUi(<Start initial={startDraft({ kind: "sunrise", offset_min: 30 })} />);
  const group = screen.getByRole("group", { name: "Start" });
  expect(text(group.querySelector(".field-resolved"))).toBe("→ 07:05 today");
  const offset = screen.getByLabelText("Minutes from sunrise (negative is before)") as HTMLInputElement;
  expect(offset.value).toBe("30");
  fireEvent.change(offset, { target: { value: "-45" } });
  expect(text(group.querySelector(".field-resolved"))).toBe("→ 05:50 today");
  fireEvent.change(offset, { target: { value: "300" } });
  expect(offset.getAttribute("aria-invalid")).toBe("true");
  expect(screen.getByRole("alert").textContent).toBe("Must be −240–240 min");
  expect(group.querySelector(".field-resolved")).toBeNull();
  fireEvent.change(screen.getByLabelText("Start: at a clock time or from the sun"), { target: { value: "clock" } });
  const clock = screen.getByLabelText("Clock time (HH:MM)") as HTMLInputElement;
  expect(clock.getAttribute("type")).toBe("time");
  expect(clock.value).toBe("07:00");
  expect(screen.queryByRole("alert")).toBeNull();
});

test("TimeField drafts read back to the ScheduleStart the API takes", () => {
  expect(startFromDraft(startDraft({ kind: "sunset", offset_min: -240 }))).toEqual({ start: { kind: "sunset", offset_min: -240 } });
  expect(startFromDraft({ kind: "solar_noon", clock: "07:00", offset: "241" })).toEqual({ error: "Must be −240–240 min" });
  expect(startFromDraft({ kind: "sunrise", clock: "07:00", offset: "1.5" })).toEqual({ error: "Must be −240–240 min" });
  expect(startFromDraft({ kind: "clock", clock: "07:30", offset: "0" })).toEqual({ start: { kind: "clock", clock: "07:30" } });
  expect(startFromDraft({ kind: "clock", clock: "7:30", offset: "0" })).toEqual({ error: "Enter a time as HH:MM" });
  expect(startDraft(null)).toEqual({ kind: "sunrise", clock: "07:00", offset: "0" });
  expect(resolvedStart({ kind: "sunset", clock: "", offset: "0" }, null)).toBeNull();
});

test("weekday chips mon to sun keep at least one day, in week order", () => {
  const seen: Weekday[][] = [];
  function Days() {
    const [days, setDays] = useState<Weekday[]>(["wed", "mon"]);
    return <WeekdayChips value={days} onChange={(next) => { seen.push(next); setDays(next); }} />;
  }
  renderUi(<Days />);
  const chip = (name: string) => screen.getByRole("button", { name });
  expect(chip("Monday").getAttribute("aria-pressed")).toBe("true");
  expect(chip("Tuesday").getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(chip("Friday"));
  fireEvent.click(chip("Monday"));
  fireEvent.click(chip("Wednesday"));
  expect(seen).toEqual([["mon", "wed", "fri"], ["wed", "fri"], ["fri"]]);
  // The last day left cannot be turned off.
  fireEvent.click(chip("Friday"));
  expect(seen).toHaveLength(3);
  expect(chip("Friday").getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByText("At least one day")).toBeTruthy();
});

test("the form controls use tokens only: no colour literal and no inline style in their source", () => {
  const directory = dirname(fileURLToPath(import.meta.url));
  const sources = readdirSync(directory).filter((name) => /\.tsx?$/.test(name) && !name.includes(".test."));
  expect(sources).toEqual(expect.arrayContaining(["confirm-dialog.tsx", "edit-drawer.tsx", "manual-toggle.tsx", "number-field.tsx", "reason.tsx", "select.tsx", "switch.tsx", "time-field.tsx"]));
  for (const name of sources) {
    const code = readFileSync(join(directory, name), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
    expect(code, name).not.toMatch(/#[0-9a-f]{3,8}\b/i);
    expect(code, name).not.toMatch(/\sstyle=\{/);
  }
});
