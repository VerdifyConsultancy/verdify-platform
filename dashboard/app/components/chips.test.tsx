// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Chips } from "./chips";
import { renderUi } from "./test-render";

afterEach(cleanup);

test("link chips mark the current one with aria-current", () => {
  const { container } = renderUi(<Chips label="Range" items={[{ key: "24h", label: "24 h", to: "?range=24h", current: true }, { key: "7d", label: "7 d", to: "?range=7d" }]} />);
  expect(container.querySelector("ul")?.className).toBe("chips");
  expect(container.querySelector("ul")?.getAttribute("aria-label")).toBe("Range");
  expect(screen.getByRole("link", { name: "24 h" }).getAttribute("aria-current")).toBe("true");
  expect(screen.getByRole("link", { name: "7 d" }).hasAttribute("aria-current")).toBe(false);
  expect(screen.getByRole("link", { name: "7 d" }).getAttribute("href")).toBe("/?range=7d");
});

test("toggle chips are buttons with aria-pressed", () => {
  const onToggle = vi.fn();
  const { container } = renderUi(<Chips label="Metrics" small items={[{ key: "vpd", label: "VPD", pressed: true, onToggle }, { key: "rh", label: "RH", pressed: false, onToggle }]} />);
  expect(container.querySelector("ul")?.className).toBe("chips chips-sm");
  expect(screen.getByRole("button", { name: "VPD" }).getAttribute("aria-pressed")).toBe("true");
  const rh = screen.getByRole("button", { name: "RH" });
  expect(rh.getAttribute("aria-pressed")).toBe("false");
  expect(rh.className).toBe("chip");
  fireEvent.click(rh);
  expect(onToggle).toHaveBeenCalledTimes(1);
});
