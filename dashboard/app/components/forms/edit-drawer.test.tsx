// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The one edit drawer (#1125, design system §4.3): a native modal <dialog>, focus in and back, Escape and × without a
// prompt, Tab kept inside, read-only for viewers, Save sent once, and the Graphs link only for °F, kPa and %.
import { useState } from "react";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Session } from "../../lib/session";
import { Button } from "../button";
import { renderUi } from "../test-render";
import { EditDrawer, graphLink, ManualToggle, NumberField, Reason } from "./index";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const OPERATOR = { role: "operator", writesEnabled: true } as const;
const dialog = () => document.querySelector("dialog")!;

function Screen({ onSave = () => {}, saving = false, unit = "kPa", readOnly = false }: { onSave?: () => void; saving?: boolean; unit?: string | null; readOnly?: boolean }) {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("1.60");
  const [manual, setManual] = useState(true);
  const [reason, setReason] = useState("");
  return <>
    <button type="button" onClick={() => setOpen(true)}>Edit</button>
    <EditDrawer open={open} onClose={() => setOpen(false)} title="Edit setting" eyebrow="PLANNER POLICY · mister_engage_kpa" heading="VPD Mister Engage"
      status="Device 1.60 kPa · CONFIRMED" graph={{ param: "mister_engage_kpa", unit }} onSave={onSave} saving={saving} readOnly={readOnly}
      actions={<Button write>Release to auto</Button>}>
      <NumberField label="New value" value={value} onChange={setValue} min={0.5} max={2.5} step={0.05} unit={unit} />
      <ManualToggle checked={manual} onChange={setManual} />
      <Reason value={reason} onChange={setReason} />
    </EditDrawer>
  </>;
}
function open(session: Partial<Session> = OPERATOR, props: Parameters<typeof Screen>[0] = {}) {
  renderUi(<Screen {...props} />, session);
  const opener = screen.getByRole("button", { name: "Edit" });
  opener.focus();
  fireEvent.click(opener);
  return opener;
}

test("opens as a modal <dialog> with showModal(), focus on the first field, and its title and item", () => {
  const showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute("open", ""); });
  const close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute("open"); });
  Object.assign(HTMLDialogElement.prototype, { showModal, close });
  try {
    open();
    expect(showModal).toHaveBeenCalledTimes(1);
    expect(dialog().open).toBe(true);
    expect(dialog().getAttribute("aria-labelledby")).toBe(screen.getByRole("heading", { name: "Edit setting" }).id);
    expect(document.activeElement).toBe(screen.getByLabelText("New value"));
    expect(dialog().textContent).toContain("PLANNER POLICY · mister_engage_kpa");
    expect(dialog().textContent).toContain("Device 1.60 kPa · CONFIRMED");
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(close).toHaveBeenCalledTimes(1);
    expect(dialog().open).toBe(false);
  } finally {
    delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
    delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).close;
  }
});

test("Tab and Shift+Tab stay inside the drawer", () => {
  open();
  const close = screen.getByRole("button", { name: "Close" });
  const save = screen.getByRole("button", { name: "Save" });
  save.focus();
  fireEvent.keyDown(save, { key: "Tab" });
  expect(document.activeElement).toBe(close);
  fireEvent.keyDown(close, { key: "Tab", shiftKey: true });
  expect(document.activeElement).toBe(save);
  // In between, Tab is the browser's.
  const field = screen.getByLabelText("New value");
  field.focus();
  expect(fireEvent.keyDown(field, { key: "Tab" })).toBe(true);
});

test("Escape and × close at once without a prompt, and focus returns to the opener", () => {
  const prompt = vi.spyOn(window, "confirm");
  const opener = open();
  fireEvent.keyDown(screen.getByLabelText("New value"), { key: "Escape" });
  expect(dialog().hasAttribute("open")).toBe(false);
  expect(document.activeElement).toBe(opener);
  fireEvent.click(opener);
  expect(dialog().hasAttribute("open")).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(dialog().hasAttribute("open")).toBe(false);
  expect(document.activeElement).toBe(opener);
  // The dialog's own cancel event (Escape in a browser) closes it the same way.
  fireEvent.click(opener);
  fireEvent(dialog(), new Event("cancel", { cancelable: true }));
  expect(dialog().hasAttribute("open")).toBe(false);
  expect(prompt).not.toHaveBeenCalled();
});

test("Save submits once; while saving it reads \"Sending…\" and Enter in a field sends nothing more", () => {
  const onSave = vi.fn();
  open(OPERATOR, { onSave });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(onSave).toHaveBeenCalledTimes(1);
  cleanup();
  const busy = vi.fn();
  open(OPERATOR, { onSave: busy, saving: true });
  expect(screen.getByRole("button", { name: "Sending…" }).hasAttribute("disabled")).toBe(true);
  fireEvent.submit(screen.getByLabelText("New value").closest("form")!);
  expect(busy).not.toHaveBeenCalled();
});

test("a viewer gets it read-only: the values, no Save; with changes switched off an operator's fields and Save are disabled", () => {
  const onSave = vi.fn();
  open({ role: "viewer", writesEnabled: true }, { onSave });
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect((screen.getByLabelText("New value") as HTMLInputElement).value).toBe("1.60");
  expect(screen.getByLabelText("New value").matches(":disabled")).toBe(true);
  expect(screen.getByRole("checkbox", { name: "Let Iris change this" }).matches(":disabled")).toBe(true);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Done" }));
  fireEvent.submit(dialog().querySelector("form")!);
  expect(onSave).not.toHaveBeenCalled();
  cleanup();
  open({ role: "operator", writesEnabled: false }, { onSave });
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByLabelText("New value").matches(":disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(false);
});

test("\"Graph this setting ›\" shows only for a unit a Graphs panel draws (°F, kPa, %)", () => {
  open();
  expect(screen.getByRole("link", { name: "Graph this setting" }).getAttribute("href")).toBe("/graphs?set=mister_engage_kpa");
  expect(screen.getByRole("link", { name: "Graph this setting" }).textContent).toBe("Graph this setting ›");
  cleanup();
  open(OPERATOR, { unit: "s" });
  expect(screen.queryByRole("link", { name: /Graph this setting/ })).toBeNull();
  expect(graphLink("fog_rh_ceiling_pct", "%")).toBe("/graphs?set=fog_rh_ceiling_pct");
  expect(graphLink("temp_low", "degF")).toBe("/graphs?set=temp_low");
  expect(graphLink("irrig_wall_duration_min", "min")).toBeNull();
  expect(graphLink("sw_gl_main_auto_mode", null)).toBeNull();
});

test("readOnly (a setting the API serves with writable false): read-only for an operator too, with Done and no Save or actions", () => {
  const onSave = vi.fn();
  open(OPERATOR, { onSave, readOnly: true });
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Release to auto" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  expect(screen.getByLabelText("New value").matches(":disabled")).toBe(true);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Done" }));
  fireEvent.submit(dialog().querySelector("form")!);
  expect(onSave).not.toHaveBeenCalled();
  cleanup();
  // Without it, an operator keeps Cancel, the actions and Save.
  open(OPERATOR);
  expect(screen.getByRole("button", { name: "Release to auto" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
});

test("what reads alongside the fields sits under them and outside their fieldset, so read-only never disables it", () => {
  renderUi(<EditDrawer open onClose={() => {}} title="Setting" onSave={() => {}} readOnly below={<button type="button">Retry</button>}>
    <NumberField label="New value" value="1.60" onChange={() => {}} />
  </EditDrawer>, OPERATOR);
  expect(screen.getByLabelText("New value").matches(":disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Retry" }).matches(":disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "Retry" }).closest("fieldset")).toBeNull();
  expect(screen.getByRole("button", { name: "Retry" }).closest(".drawer-body")).not.toBeNull();
});
