// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The one Stop all water (redesign §2.5): operators only, filled red, one tap, never disabled by the writes flag; compact
// reads "■ Stop all" on phones; a caller's own Stop control drives it when given.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, expect, test, vi } from "vitest";
import type { Session } from "../lib/session";
import { StopAllWater } from "./stop-all-water";
import { renderUi, text } from "./test-render";
import { ToastProvider } from "./toast";

afterEach(cleanup);
const mount = (ui: ReactElement, session: Partial<Session>) => renderUi(<QueryClientProvider client={new QueryClient()}><ToastProvider>{ui}</ToastProvider></QueryClientProvider>, session);
const button = () => screen.queryByRole("button", { name: "Stop all water" });

test("a viewer gets none", () => {
  mount(<StopAllWater />, { role: "viewer", writesEnabled: true });
  expect(button()).toBeNull();
  expect(document.querySelector("[data-write-control]")).toBeNull();
});

test("an operator's is filled red with its words, and stays live with changes switched off", () => {
  mount(<StopAllWater />, { role: "operator", writesEnabled: false });
  const stop = button()!;
  expect(stop.className).toBe("btn btn-stop stop-all-water");
  expect(text(stop)).toBe("■ Stop all water");
  expect(stop.querySelector("[aria-hidden=true]")?.textContent).toBe("■ ");
  expect(stop.hasAttribute("disabled")).toBe(false);
  expect(stop.getAttribute("data-write-control")).toBe("true");
});

test("compact hides the last word on phones (the stylesheet), wide is full width there; a caller's control is the one used", () => {
  const stop = vi.fn();
  mount(<StopAllWater compact wide control={{ stop, sending: false }} />, { role: "operator", writesEnabled: true });
  const shown = button()!;
  expect(shown.className).toBe("btn btn-stop btn-wide stop-all-water stop-all-water-compact");
  expect(text(shown.querySelector(".stop-all-water-word"))).toBe("water");
  fireEvent.click(shown);
  expect(stop).toHaveBeenCalledTimes(1);
  cleanup();
  mount(<StopAllWater control={{ stop, sending: true }} />, { role: "operator", writesEnabled: true });
  expect(text(screen.getByRole("button"))).toBe("Sending…");
  expect(screen.getByRole("button").hasAttribute("disabled")).toBe(true);
});
