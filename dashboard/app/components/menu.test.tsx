// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// The ⋯ menu and the account menu (redesign §4.5): a native disclosure that Escape, a press outside it and choosing an
// item close, with no style attribute and no ARIA menu roles.
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, expect, test } from "vitest";
import { returnFocus, useModalDialog } from "./forms/modal";
import { Menu } from "./menu";
import { renderUi } from "./test-render";

afterEach(cleanup);

function mount() {
  return renderUi(<div>
    <p>Outside</p>
    <Menu name="More for Wall drip">
      <a href="/activity/log?target=L01">Activity for Wall drip</a>
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
  expect(summary().querySelector(".screen-reader")?.textContent).toBe("More for Wall drip");
  expect(summary().querySelector("[aria-hidden=true]")?.className).toBe("glyph glyph-more");
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
  fireEvent.click(screen.getByRole("link", { name: "Activity for Wall drip" }));
  expect(details().open).toBe(false);
});

test("a button menu shows its own label and the disclosure chevron; start aligns its panel to the left edge", () => {
  renderUi(<Menu kind="button" align="start" label={<span>Account</span>}><p>Signed in</p></Menu>);
  expect(details().className).toBe("menu menu-start");
  expect(summary().textContent).toBe("Account");
  expect(summary().hasAttribute("aria-label")).toBe(false);
});

/** A row's ⋯ with an item that opens a drawer, as Options… and Pause until… do on Water. */
function WithDrawer() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDialogElement>(null);
  const { onKeyDown, onCancel } = useModalDialog(ref, open, () => setOpen(false));
  return <>
    <Menu name="More for Wall drip"><button type="button" onClick={() => setOpen(true)}>Options…</button></Menu>
    <dialog ref={ref} aria-label="Options" onKeyDown={onKeyDown} onCancel={onCancel}><button type="button" onClick={() => setOpen(false)}>Close</button></dialog>
  </>;
}

test("an item that opens a drawer: the menu closes, and closing the drawer puts focus back on the ⋯ button, never on the page", () => {
  renderUi(<WithDrawer />);
  openIt();
  const item = screen.getByRole("button", { name: "Options…" });
  item.focus();
  fireEvent.click(item);
  expect(details().open).toBe(false);
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));
  act(() => { fireEvent.click(screen.getByRole("button", { name: "Close" })); });
  expect(document.activeElement).toBe(summary());
});

test("an opener that can no longer take focus hands it to its closed menu's button", () => {
  mount();
  const item = screen.getByRole("button", { name: "Options…" });
  // A hidden item refuses focus in a browser; the stand-in refuses it the same way.
  item.focus = () => {};
  returnFocus(item);
  expect(document.activeElement).toBe(summary());
});
