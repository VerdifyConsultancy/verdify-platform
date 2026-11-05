// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
// Popover (redesign §5.14): a dialog under its opener from 861 px, a bottom sheet at 860 px and below; focus moves in and
// returns to the opener; Escape, Close and a press outside close it; no style attribute in its markup.
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { useRef, useState } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Popover } from "./popover";
import { renderUi } from "./test-render";

const narrow = (matches: boolean) => vi.stubGlobal("matchMedia", (query: string) => ({ matches: query === "(max-width: 860px)" ? matches : false, media: query, addEventListener() {}, removeEventListener() {} }));
beforeEach(() => narrow(false));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Harness() {
  const opener = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  return <>
    <p>Outside</p>
    <button ref={opener} type="button" onClick={() => setOpen(true)}>07:31 Wall drip</button>
    <Popover open={open} onClose={() => setOpen(false)} anchor={opener.current} title="Wall drip · Wall MWF">
      <p>Done · ok · 12.0 min · 9 gal</p>
      <a href="/water/history?line=L01">Runs of Wall drip</a>
    </Popover>
  </>;
}
const dialog = () => document.querySelector<HTMLDialogElement>("dialog.popover");

test("from 861 px it opens under its opener, named by its title, focus inside; Escape closes it and focus returns", () => {
  renderUi(<Harness />);
  expect(dialog()).toBeNull();
  const opener = screen.getByRole("button", { name: "07:31 Wall drip" });
  opener.focus();
  fireEvent.click(opener);
  const shown = dialog()!;
  expect(shown.className).toBe("popover popover-anchored");
  expect(shown.hasAttribute("open")).toBe(true);
  expect(screen.getByRole("dialog", { name: "Wall drip · Wall MWF" })).toBe(shown);
  expect(shown.contains(document.activeElement)).toBe(true);
  // Placed through CSSOM custom properties, never a style attribute in the markup React writes.
  expect(shown.style.getPropertyValue("--pop-top")).toMatch(/px$/);
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(dialog()).toBeNull();
  expect(document.activeElement).toBe(opener);
});

test("Close and a press outside close it; a press inside does not", () => {
  renderUi(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "07:31 Wall drip" }));
  fireEvent.pointerDown(screen.getByText("Done · ok · 12.0 min · 9 gal"));
  expect(dialog()).not.toBeNull();
  fireEvent.pointerDown(screen.getByText("Outside"));
  expect(dialog()).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "07:31 Wall drip" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(dialog()).toBeNull();
});

test("a press inside another open dialog (the editor its Edit opened over it) leaves it open, so closing that dialog comes back to it", () => {
  renderUi(<><Harness /><dialog open aria-label="Edit schedule"><label>Label<input defaultValue="Wall MWF" /></label></dialog></>);
  fireEvent.click(screen.getByRole("button", { name: "07:31 Wall drip" }));
  fireEvent.pointerDown(screen.getByLabelText("Label"));
  expect(dialog()).not.toBeNull();
  // A press on the page itself still closes it.
  fireEvent.pointerDown(screen.getByText("Outside"));
  expect(dialog()).toBeNull();
});

test("at 860 px and below it is a bottom sheet in a modal dialog", () => {
  narrow(true);
  renderUi(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "07:31 Wall drip" }));
  expect(dialog()?.className).toBe("popover popover-sheet");
  expect(screen.getByRole("link", { name: "Runs of Wall drip" }).getAttribute("href")).toBe("/water/history?line=L01");
});
