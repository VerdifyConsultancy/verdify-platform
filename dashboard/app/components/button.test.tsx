// @vitest-environment jsdom
// SPDX-License-Identifier: MIT
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { Button } from "./button";
import { renderUi } from "./test-render";

afterEach(cleanup);
const OPERATOR = { role: "operator", writesEnabled: true } as const;

test("a 44 px .btn with a verb and its object; primary and stop variants", () => {
  const onClick = vi.fn();
  renderUi(<Button onClick={onClick}>Retry</Button>);
  const button = screen.getByRole("button", { name: "Retry" });
  expect(button.className).toBe("btn");
  expect(button.getAttribute("type")).toBe("button");
  expect(button.hasAttribute("data-write-control")).toBe(false);
  fireEvent.click(button);
  expect(onClick).toHaveBeenCalledTimes(1);
  cleanup();
  renderUi(<Button variant="primary" type="submit">Save</Button>);
  expect(screen.getByRole("button", { name: "Save" }).className).toBe("btn btn-primary");
  cleanup();
  renderUi(<Button write variant="stop" wide>Stop all water</Button>, OPERATOR);
  expect(screen.getByRole("button", { name: "Stop all water" }).className).toBe("btn btn-stop btn-wide");
});

test("busy reads \"Sending…\" and is disabled, so a second tap sends nothing", () => {
  const onClick = vi.fn();
  renderUi(<Button write busy onClick={onClick}>Run wall drip</Button>, OPERATOR);
  const button = screen.getByRole("button", { name: "Sending…" });
  expect(button.hasAttribute("disabled")).toBe(true);
  fireEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
  expect(screen.queryByText(/spin/i)).toBeNull();
});

test("a write control carries data-write-control and renders only for operators", () => {
  const { container } = renderUi(<Button write>Run wall drip</Button>, OPERATOR);
  expect(container.querySelector("button")?.getAttribute("data-write-control")).toBe("true");
  cleanup();
  for (const session of [{ role: "viewer", writesEnabled: true }, { role: "viewer", writesEnabled: false }, {}] as const) {
    const viewer = renderUi(<><Button write>Run wall drip</Button><Button write variant="stop">Stop all water</Button></>, session).container;
    expect(viewer.querySelector("[data-write-control]")).toBeNull();
    expect(viewer.textContent).toBe("");
    cleanup();
  }
});

test("with changes switched off an operator's write controls are disabled, but Stop stays live", () => {
  renderUi(<><Button write>Save</Button><Button write variant="stop">Stop all water</Button><Button>Retry</Button></>, { role: "operator", writesEnabled: false });
  expect(screen.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
  expect(screen.getByRole("button", { name: "Stop all water" }).hasAttribute("disabled")).toBe(false);
  expect(screen.getByRole("button", { name: "Retry" }).hasAttribute("disabled")).toBe(false);
});
