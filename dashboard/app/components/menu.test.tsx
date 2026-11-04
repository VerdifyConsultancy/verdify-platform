// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The ⋯ menu and the account menu (redesign §4.5): a native disclosure that Escape, a press outside it and choosing an
// item close, with no style attribute and no ARIA menu roles.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { Menu } from "./menu";
import { renderUi } from "./test-render";

afterEach(cleanup);

function mount() {
  return renderUi(<div>
    <p>Outside</p>
    <Menu name="More for L01 Wall drip">
      <a href="/activity/log?target=L01">Activity for L01</a>
      <button type="button">Options…</button>
      <button type="button" data-menu-keep="">Enabled</button>
    </Menu>
  </div>);
}
const details = () => document.querySelector<HTMLDetailsElement>("details.menu")!;
const summary = () => details().querySelector("summary")!;
const openIt = () => { details().open = true; };

test("a ⋯ button named in words over a panel of links and buttons; a disclosure, never an ARIA menu", () => {
  const { container } = mount();
  expect(details().className).toBe("menu menu-dots");
  // Named in words inside the button; the ⋯ is decoration.
  expect(summary().querySelector(".screen-reader")?.textContent).toBe("More for L01 Wall drip");
  expect(summary().querySelector("[aria-hidden=true]")?.textContent).toBe("⋯");
  expect(summary().hasAttribute("aria-label")).toBe(false);
  expect(container.querySelector("[role=menu], [role=menuitem]")).toBeNull();
  expect(container.querySelector("[style]")).toBeNull();
  expect(details().open).toBe(false);
});

test("Escape closes it and puts focus back on its button", () => {
  mount();
  openIt();
  const item = screen.getByRole("button", { name: "Options…" });
  item.focus();
  fireEvent.keyDown(item, { key: "Escape" });
  expect(details().open).toBe(false);
  expect(document.activeElement).toBe(summary());
});

test("a press outside closes it; a press inside does not", () => {
  mount();
  openIt();
  fireEvent.pointerDown(screen.getByRole("button", { name: "Enabled" }));
  expect(details().open).toBe(true);
  fireEvent.pointerDown(screen.getByText("Outside"));
  expect(details().open).toBe(false);
});

test("choosing an item closes it, unless the item keeps it open (a switch that toggles in place)", () => {
  mount();
  openIt();
  fireEvent.click(screen.getByRole("button", { name: "Enabled" }));
  expect(details().open).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Options…" }));
  expect(details().open).toBe(false);
  openIt();
  fireEvent.click(screen.getByRole("link", { name: "Activity for L01" }));
  expect(details().open).toBe(false);
});

test("a button menu shows its own label and the disclosure chevron; start aligns its panel to the left edge", () => {
  renderUi(<Menu kind="button" align="start" label={<span>Account</span>}><p>Signed in</p></Menu>);
  expect(details().className).toBe("menu menu-start");
  expect(summary().textContent).toBe("Account");
  expect(summary().hasAttribute("aria-label")).toBe(false);
});
