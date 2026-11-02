// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SUCCESS_TOAST_MS, ToastProvider, useToast, type ToastApi } from "./toast";

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

let api: ToastApi;
function Grab() { api = useToast(); return null; }
function mount() { return render(<ToastProvider><Grab /></ToastProvider>); }
const show = (options: Parameters<ToastApi["show"]>[0]) => act(() => { api.show(options); });
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

test("a success toast is polite and leaves after 5 s", () => {
  const { container } = mount();
  show({ message: "Requested 1.20 kPa for VPD Mister Engage" });
  const region = screen.getByRole("status");
  expect(region.textContent).toContain("Requested 1.20 kPa for VPD Mister Engage");
  expect(container.ownerDocument.querySelector(".toasts")).not.toBeNull();
  advance(SUCCESS_TOAST_MS - 1);
  expect(region.textContent).toContain("Requested 1.20 kPa");
  advance(1);
  expect(region.textContent).toBe("");
});

test("a failure is role=\"alert\" and stays until dismissed", () => {
  mount();
  show({ kind: "failure", message: "Couldn't save: Must be 0.50–2.50 kPa" });
  const alert = screen.getByRole("alert");
  advance(60_000);
  expect(alert.textContent).toContain("Couldn't save");
  expect(screen.getByRole("status").textContent).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
  expect(alert.textContent).toBe("");
});

test("a toast with guidance or buttons stays until dismissed", () => {
  mount();
  show({ message: "Stopped every running valve.", detail: "VPD misting and fog can still start on their own.", actions: <button type="button">Disable L01</button> });
  show({ message: "Saved", detail: "Close the spigot to stop water now." });
  advance(60_000);
  const region = screen.getByRole("status");
  expect(region.textContent).toContain("Stopped every running valve.");
  expect(region.textContent).toContain("Close the spigot");
  expect(screen.getByRole("button", { name: "Disable L01" })).toBeTruthy();
  fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
  expect(region.textContent).not.toContain("Stopped every running valve.");
  expect(region.textContent).toContain("Close the spigot");
});

test("useToast needs a ToastProvider", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => render(<Grab />)).toThrow("useToast needs a ToastProvider above it");
});
