// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Button } from "./button";
import { Card } from "./card";
import { renderUi, text } from "./test-render";

afterEach(cleanup);

test("a card with an eyebrow, heading and its own actions", () => {
  const { container } = renderUi(<Card eyebrow="Water today" title="Center 14 min" actions={<Button>Retry</Button>}><p>Wall 0</p></Card>);
  const card = container.querySelector(".card")!;
  expect(card.tagName).toBe("DIV");
  expect(text(card.querySelector(".card-head .eyebrow"))).toBe("Water today");
  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("Center 14 min");
  expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  expect(text(card)).toContain("Wall 0");
});

test("an interactive card is one link", () => {
  const { container } = renderUi(<Card to="/water" title="Water" headingLevel={3}><p>Center 14 min</p></Card>);
  const link = screen.getByRole("link");
  expect(link.className).toBe("card card-link");
  expect(link.getAttribute("href")).toBe("/water");
  expect(link.querySelector("h3")?.textContent).toBe("Water");
  expect(container.querySelectorAll("a, button")).toHaveLength(1);
});

test("a card with no heading has no head", () => {
  expect(renderUi(<Card><p>Plain</p></Card>).container.querySelector(".card-head")).toBeNull();
});
