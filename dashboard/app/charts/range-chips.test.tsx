// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, test } from "vitest";
import { RANGES, RangeChips } from "./range-chips";

afterEach(cleanup);

test("24 h, 72 h, 7 d and 30 d are links on weather's chips, the current one marked", () => {
  render(<MemoryRouter><RangeChips current="7d" href={(range) => `/graphs?range=${range}&set=mister_engage_kpa`} /></MemoryRouter>);
  const list = screen.getByRole("list", { name: "Range" });
  expect(list.className).toBe("chips chips-sm range-chips");
  const links = within(list).getAllByRole("link");
  expect(links.map((link) => link.textContent)).toEqual(["24 h", "72 h", "7 d", "30 d"]);
  expect(links.map((link) => link.getAttribute("aria-current"))).toEqual([null, null, "page", null]);
  expect(links[0].getAttribute("href")).toBe("/graphs?range=24h&set=mister_engage_kpa");
  expect(RANGES.map((range) => range.seconds)).toEqual([86_400, 259_200, 604_800, 2_592_000]);
});

test("a custom window marks no chip", () => {
  render(<MemoryRouter><RangeChips current={null} href={(range) => `?range=${range}`} /></MemoryRouter>);
  expect(screen.getAllByRole("link").every((link) => !link.hasAttribute("aria-current"))).toBe(true);
});
